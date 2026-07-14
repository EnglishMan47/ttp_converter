"""
queue_manager.py — очередь обработки книг для веб-интерфейса.

Модель работы двухфазная: добавленная книга НЕ обрабатывается
автоматически — она находится в состоянии ADDED, пока пользователь
явно не нажмёт «Начать» (для книги или для всех). Это исключает
случайный запуск тяжёлого конвейера при простой загрузке файлов.

Состояния книги:
    ADDED      — загружена, ожидает явного запуска; можно менять её
                 индивидуальные настройки (DPI, страниц на проход)
    QUEUED     — поставлена в очередь, ждёт своей очереди
    PROCESSING — обрабатывается
    STOPPED    — остановлена пользователем (чекпоинты сохранены)
    DONE       — успешно завершена
    ERROR      — завершилась с ошибкой

Гарантии очереди:
    - строгий FIFO: книги обрабатываются в порядке постановки;
    - остановка или сбой одной книги не блокирует следующие;
    - повторный запуск остановленной книги ставит её В КОНЕЦ текущей
      очереди (обработка продолжится с чекпоинта);
    - «Остановить всё» прерывает текущую книгу и возвращает все
      QUEUED-книги в состояние ADDED (для продолжения нужен явный
      повторный запуск).

Индивидуальные настройки книги: dpi, batch_size. Общие настройки
партии (движок, предобработка, флаги вывода) передаются при создании
менеджера и применяются ко всем книгам.
"""

from __future__ import annotations

import queue
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ADDED = "added"
QUEUED = "queued"
PROCESSING = "processing"
STOPPED = "stopped"
DONE = "done"
ERROR = "error"


# веса этапов для оценки общего прогресса: этап 2 (распознавание)
# занимает основную часть времени
_STAGE_WEIGHTS = {1: 0.15, 2: 0.70, 3: 0.15}


def _stage1_checkpoint_ok(pdf_path: Path) -> bool:
    """Чекпоинт этапа 1 пригоден: PDF открывается и содержит страницы."""
    try:
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            return False
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            return doc.page_count > 0
    except Exception:
        return False


@dataclass
class Book:
    id: str
    name: str
    folder: str                      # папка с TIFF
    status: str = ADDED
    dpi: int = 150                   # индивидуальная настройка
    batch_size: int = 4              # индивидуальная настройка
    progress: dict = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    message: str = ""
    output_pdf: str = ""
    review_pdf: str = ""
    report_txt: str = ""
    report: dict = field(default_factory=dict)
    # ── время ──
    run_started_ts: float = 0.0      # старт текущего прогона
    elapsed_prev: float = 0.0        # накоплено в прошлых прогонах (до остановок)
    finished_at: str = ""            # "ЧЧ:ММ:СС ДД.ММ.ГГГГ" по завершении
    # ── оценка остатка (по скорости текущего этапа) ──
    stage_no: int = 0                # какой этап выполняется (1..3)
    stage_started_ts: float = 0.0    # когда этап начался
    stage_base_pct: int = 0          # прогресс этапа на момент его старта
    eta_snapshot: float | None = None  # остаток, оценённый в последний тик
    eta_snapshot_ts: float = 0.0

    def elapsed_sec(self) -> float:
        """Сколько времени обработка книги уже заняла (суммарно)."""
        cur = (time.time() - self.run_started_ts
               if self.status == PROCESSING and self.run_started_ts else 0.0)
        return self.elapsed_prev + cur

    def enter_stage(self, stage: int) -> None:
        """Отметка начала этапа: якорь для измерения его скорости."""
        self.stage_no = stage
        self.stage_started_ts = time.time()
        self.stage_base_pct = self.progress[stage]
        self.eta_snapshot = None

    def note_progress(self, stage: int, pct: int) -> None:
        """Обновляет прогресс этапа и пересчитывает оценку остатка."""
        pct = max(0, min(100, pct))
        if pct == self.progress[stage]:
            return
        self.progress[stage] = pct
        # Скорость меряется только по текущему этапу: прежняя смешанная
        # оценка (elapsed по всем этапам сразу) занижала остаток, пока
        # шёл быстрый этап 1, и затем всё распознавание «росла» вверх.
        gained = pct - self.stage_base_pct
        t = time.time() - self.stage_started_ts
        if stage != self.stage_no or stage == 1 or gained <= 0 or t < 2:
            return                        # этап 1 скоротечен — не якорь
        rate = gained / t                 # процентов этапа в секунду
        rem = (100 - pct) / rate
        if stage == 2:                    # впереди ещё сборка PDF
            rem += (_STAGE_WEIGHTS[3] / _STAGE_WEIGHTS[2]) * (100 / rate)
        self.eta_snapshot = rem
        self.eta_snapshot_ts = time.time()

    def eta_sec(self) -> float | None:
        """
        Приблизительный остаток; None, пока оценивать рано. Между
        обновлениями прогресса оценка убывает в реальном времени.
        """
        if self.status != PROCESSING or self.eta_snapshot is None:
            return None
        return max(self.eta_snapshot - (time.time() - self.eta_snapshot_ts),
                   1.0)


class QueueManager:
    def __init__(self, output_dir: str | Path, settings: dict):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings = dict(settings)   # общие настройки партии
        self.books: dict[str, Book] = {}
        self._order: list[str] = []      # порядок карточек в интерфейсе
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._stop_current = threading.Event()
        self._current_id: str | None = None
        self._thread: threading.Thread | None = None
        self.reports: list[dict] = []
        # ── время партии ──
        self.batch_started_ts: float = 0.0
        self.batch_finished_at: str = ""
        self._batch_done_durations: list[float] = []

    # ── управление книгами ───────────────────────────────────────────

    def add_book(self, name: str, folder: str,
                 dpi: int = 150, batch_size: int = 4) -> Book:
        """Добавляет книгу в состоянии ADDED (обработка НЕ начинается)."""
        book = Book(id=uuid.uuid4().hex[:8], name=name, folder=str(folder),
                    dpi=dpi, batch_size=batch_size)
        with self._lock:
            self.books[book.id] = book
            self._order.append(book.id)
        return book

    def update_book_settings(self, book_id: str,
                             dpi: int | None = None,
                             batch_size: int | None = None) -> None:
        with self._lock:
            b = self.books.get(book_id)
            if b and b.status == ADDED:
                if dpi is not None:
                    b.dpi = int(dpi)
                if batch_size is not None:
                    b.batch_size = int(batch_size)

    def start_book(self, book_id: str) -> None:
        """Явный запуск: ADDED/STOPPED/ERROR → QUEUED, в КОНЕЦ очереди."""
        with self._lock:
            b = self.books.get(book_id)
            if not b or b.status in (QUEUED, PROCESSING):
                return
            b.status = QUEUED
            b.message = "В очереди"
            self._mark_batch_start()
            self._queue.put(book_id)
        self._ensure_worker()

    def start_all(self) -> None:
        """Запуск всех книг в состоянии ADDED, в порядке добавления."""
        with self._lock:
            ids = [i for i in self._order
                   if self.books[i].status == ADDED]
            if ids:
                self._mark_batch_start()
            for i in ids:
                self.books[i].status = QUEUED
                self.books[i].message = "В очереди"
                self._queue.put(i)
        if ids:
            self._ensure_worker()

    def stop_book(self, book_id: str) -> None:
        """Остановка конкретной книги (текущей или ожидающей)."""
        with self._lock:
            b = self.books.get(book_id)
            if not b:
                return
            if b.status == PROCESSING and self._current_id == book_id:
                self._stop_current.set()
                b.message = "Останавливается…"
            elif b.status == QUEUED:
                b.status = ADDED
                b.message = "Снята с очереди"

    def stop_all(self) -> None:
        """
        Прерывает текущую книгу (чекпоинт сохраняется) и возвращает все
        ожидающие книги в ADDED — автоматически они не продолжатся.
        """
        with self._lock:
            for b in self.books.values():
                if b.status == QUEUED:
                    b.status = ADDED
                    b.message = "Остановлено (нужен повторный запуск)"
            if self._current_id:
                self._stop_current.set()
        # очищаем очередь: воркер пропустит книги не в статусе QUEUED

    def remove_book(self, book_id: str) -> bool:
        """
        Закрыть карточку. Разрешено для неактивных состояний
        (ADDED / STOPPED / DONE / ERROR). Загруженные файлы книги
        удаляются; готовые PDF в папке вывода не трогаются.
        """
        with self._lock:
            b = self.books.get(book_id)
            if not b or b.status in (QUEUED, PROCESSING):
                return False
            del self.books[book_id]
            self._order.remove(book_id)
        shutil.rmtree(b.folder, ignore_errors=True)
        return True

    def ordered_books(self) -> list[Book]:
        with self._lock:
            return [self.books[i] for i in self._order]

    def is_busy(self) -> bool:
        return self._current_id is not None or not self._queue.empty()

    # ── время партии ─────────────────────────────────────────────────

    def _mark_batch_start(self) -> None:
        """Начало новой партии: первый запуск из состояния простоя."""
        if not self.is_busy() and (self.batch_finished_at
                                   or not self.batch_started_ts):
            self.batch_started_ts = time.time()
            self.batch_finished_at = ""
            self._batch_done_durations = []

    def reset_batch_stats(self) -> None:
        """Сброс статистики партии (когда список книг опустел)."""
        if not self.is_busy():
            self.batch_started_ts = 0.0
            self.batch_finished_at = ""
            self._batch_done_durations = []

    def _mark_batch_finish(self) -> None:
        if self.batch_started_ts and not self.batch_finished_at:
            self.batch_finished_at = time.strftime("%H:%M:%S %d.%m.%Y")

    def batch_stats(self) -> dict:
        """
        Общая статистика партии:
            elapsed_sec  — сколько прошло с начала партии
            eta_sec      — приблизительный остаток (None, если оценить
                           нечем или партия завершена)
            finished_at  — когда партия была завершена ("" — ещё идёт)
        Остаток = остаток текущей книги + оценка на каждую ожидающую
        (по средней длительности завершённых книг этой партии, а до
        первой завершённой — по прогнозу текущей).
        """
        if not self.batch_started_ts:
            return {"elapsed_sec": 0.0, "eta_sec": None, "finished_at": ""}
        if self.batch_finished_at:
            return {"elapsed_sec": None, "eta_sec": None,
                    "finished_at": self.batch_finished_at}

        elapsed = time.time() - self.batch_started_ts
        with self._lock:
            current = (self.books.get(self._current_id)
                       if self._current_id else None)
            queued = sum(1 for b in self.books.values()
                         if b.status == QUEUED)
        eta = None
        if current is not None:
            cur_eta = current.eta_sec()
            if cur_eta is not None:
                if self._batch_done_durations:
                    per_book = (sum(self._batch_done_durations)
                                / len(self._batch_done_durations))
                else:
                    per_book = current.elapsed_sec() + cur_eta
                eta = cur_eta + queued * per_book
        return {"elapsed_sec": elapsed, "eta_sec": eta, "finished_at": ""}

    # ── воркер ───────────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                book_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._mark_batch_finish()
                return
            with self._lock:
                b = self.books.get(book_id)
                # книга могла быть снята с очереди stop_all/stop_book
                if not b or b.status != QUEUED:
                    continue
                b.status = PROCESSING
                b.message = ""
                self._current_id = book_id
                self._stop_current.clear()
            try:
                self._process(b)
            finally:
                with self._lock:
                    self._current_id = None

    def _process(self, b: Book) -> None:
        folder = Path(b.folder)
        work = self.output_dir / f"{b.name}.work"
        work.mkdir(parents=True, exist_ok=True)
        stage1_pdf = work / "stage1.pdf"
        ocr_jsonl = work / "ocr.jsonl"
        final_pdf = self.output_dir / f"{b.name}.pdf"
        t0 = time.time()
        b.run_started_ts = t0

        # индивидуальные настройки книги поверх общих
        settings = dict(self.settings)
        settings["dpi"] = b.dpi
        settings["chandra_batch_size"] = b.batch_size

        cancelled = self._stop_current.is_set
        report: dict = {"book": b.name, "folder": b.folder,
                        "output": str(final_pdf), "status": "failed",
                        "stages": {}}

        from report import write_book_report, write_book_txt_report
        from stage_1.pipeline import ProcessingCancelled

        def prog(stage):
            def cb(pct):
                b.note_progress(stage, pct)
            return cb

        try:
            from stage_2.engines import resolve_engine
            engine, why = resolve_engine(settings.get("ocr_engine", "auto"))
            report["engine"] = engine
            b.message = f"Движок: {engine}"

            # этап 1 (чекпоинт: засчитывается только целый PDF)
            b.enter_stage(1)
            if _stage1_checkpoint_ok(stage1_pdf):
                b.progress[1] = 100
                report["stages"]["stage1"] = {"skipped": True}
            else:
                stage1_pdf.unlink(missing_ok=True)
                from stage_1.pipeline import run_pipeline
                s1 = run_pipeline(folder, stage1_pdf, settings,
                                  on_progress=prog(1),
                                  is_cancelled=cancelled)
                report["stages"]["stage1"] = s1

            # этап 2 (чекпоинт построчно)
            b.enter_stage(2)
            if engine == "chandra":
                from stage_2.jsonl_store import load_pages
                from stage_2.ocr_chandra import run_ocr

                def status(msg):
                    # статус загрузки весов; "" — загрузка окончена
                    b.message = msg or f"Движок: {engine}"

                s2 = run_ocr(stage1_pdf, ocr_jsonl, dpi=b.dpi,
                             batch_size=b.batch_size,
                             on_progress=prog(2), on_status=status,
                             is_cancelled=cancelled)
            else:
                from stage_2.jsonl_store import load_pages
                from stage_2.ocr_easy import run_ocr
                s2 = run_ocr(stage1_pdf, ocr_jsonl, dpi=b.dpi,
                             on_progress=prog(2), is_cancelled=cancelled)
            report["stages"]["stage2"] = s2
            pages_data = load_pages(ocr_jsonl)

            b.note_progress(2, 100)   # этап завершён — гарантируем 100%

            # этап 3
            b.enter_stage(3)
            from stage_3.pipeline3 import process_book
            s3 = process_book(stage1_pdf, pages_data, final_pdf, settings,
                              on_progress=prog(3), is_cancelled=cancelled)
            report["stages"]["stage3"] = s3
            b.note_progress(3, 100)

            report["status"] = "done"
            b.elapsed_prev += time.time() - b.run_started_ts
            b.run_started_ts = 0.0
            b.finished_at = time.strftime("%H:%M:%S %d.%m.%Y")
            report["elapsed_sec"] = round(b.elapsed_prev, 1)
            self._batch_done_durations.append(b.elapsed_prev)
            b.status = DONE
            b.output_pdf = str(final_pdf)
            b.review_pdf = s3.get("review_pdf", "")
            # если распознавание не удалось на страницах — это надо показать,
            # а не выдавать за чистый успех: текстовый слой будет неполным
            n_err = len(s2.get("page_errors", []))
            n_pages = s2.get("pages", 0)
            if n_pages and n_err >= n_pages:
                b.message = ("Внимание: распознать текст не удалось ни на одной "
                             "странице — в PDF нет текстового слоя. Проверьте, "
                             "что модели распознавания загружены полностью, и "
                             "запустите книгу заново.")
            elif n_err:
                b.message = (f"Готово, но распознавание не удалось на {n_err} из "
                             f"{n_pages} страниц — текстовый слой неполный. "
                             f"Файл: {final_pdf}")
            else:
                b.message = f"Файл сохранён по пути: {final_pdf}"
        except ProcessingCancelled:
            report["status"] = "stopped"
            b.elapsed_prev += time.time() - b.run_started_ts
            b.run_started_ts = 0.0
            b.status = STOPPED
            b.message = ("Остановлено. При повторном запуске обработка "
                         "продолжится с места остановки.")
        except Exception as e:
            report["error"] = f"{e}\n{traceback.format_exc()}"
            b.elapsed_prev += time.time() - b.run_started_ts
            b.run_started_ts = 0.0
            b.finished_at = time.strftime("%H:%M:%S %d.%m.%Y")
            b.status = ERROR
            b.message = f"Ошибка: {type(e).__name__}: {e}"

        try:
            write_book_report(work, report)
            p = write_book_txt_report(self.output_dir, report)
            b.report_txt = str(p)
        except OSError:
            pass
        if report["status"] == "done" and not self.settings.get(
                "keep_workdir", False):
            shutil.rmtree(work, ignore_errors=True)

        b.report = report
        self.reports.append(report)
