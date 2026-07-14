"""
web.py — веб-интерфейс системы (Streamlit).

Запуск:
    streamlit run app/web.py --server.port 8501 --server.address 0.0.0.0

Пользователь работает через браузер: загружает TIFF-файлы книги (или
ZIP с ними), при необходимости меняет индивидуальные настройки книги
(DPI, страниц на проход), явно запускает обработку и скачивает
результаты. Общие настройки партии — в боковой панели.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))

import streamlit as st

from queue_manager import (ADDED, DONE, ERROR, PROCESSING, QUEUED, STOPPED,
                           QueueManager)

st.set_page_config(page_title="Цифровая библиотека", page_icon="📚",
                   layout="wide")

# ── адаптивная вёрстка ───────────────────────────────────────────────
# Streamlit при сужении окна сжимает колонки пропорционально, из-за чего
# подписи кнопок и значения метрик ломаются посреди слова. Правила ниже
# переносят колонки целиком и ограничивают размер цифр метрик.
st.markdown("""
<style>
/* кнопка Deploy (публикация на облачных площадках Streamlit) не нужна
   пользователям системы */
[data-testid="stAppDeployButton"], .stAppDeployButton { display: none !important; }

/* подписи кнопок не разрывать посреди фразы */
.stButton button p, .stDownloadButton button p { white-space: nowrap; }

/* значения метрик: умеренный размер, моноширинные цифры, без переносов */
[data-testid="stMetricValue"] {
    font-size: clamp(1.05rem, 0.7rem + 1.1vw, 1.9rem);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}

/* на средних и узких окнах колонки переносятся на новую строку целиком,
   а не сжимаются в узкие «столбики» с изломанным текстом */
@media (max-width: 1100px) {
    div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap;
        row-gap: 0.4rem;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        flex: 1 1 auto !important;
        width: auto !important;
        min-width: fit-content !important;
        max-width: 100% !important;
    }
}
</style>
""", unsafe_allow_html=True)

STATUS_TITLES = {
    ADDED: "Добавлена, не запущена",
    QUEUED: "В очереди",
    PROCESSING: "Обрабатывается",
    STOPPED: "Остановлена",
    DONE: "Готово",
    ERROR: "Ошибка",
}
STAGE_TITLES = {1: "Этап 1 — подготовка страниц",
                2: "Этап 2 — распознавание текста",
                3: "Этап 3 — сборка PDF"}

def fmt_dur(sec: float | None) -> str:
    """Секунды → человекочитаемая длительность."""
    if sec is None:
        return "—"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s_ = divmod(rem, 60)
    if h:
        return f"{h} ч {m:02d} мин"
    if m:
        return f"{m} мин {s_:02d} с"
    return f"{s_} с"


DPI_HINT = ("DPI — плотность точек на дюйм при обработке и распознавании. "
            "150 — сбалансированное значение для обычного книжного текста; "
            "200–300 для мелкого шрифта; чем выше, тем медленнее и тяжелее "
            "файл.")


# ── общие ресурсы сеанса ─────────────────────────────────────────────

@st.cache_resource
def get_workspace() -> dict:
    root = Path(tempfile.mkdtemp(prefix="tiff2pdf_web_"))
    return {"uploads": root / "uploads", "output": root / "output"}


def get_manager() -> QueueManager:
    if "manager" not in st.session_state:
        ws = get_workspace()
        st.session_state.manager = QueueManager(ws["output"],
                                                st.session_state.settings)
    # общие настройки могли поменяться в сайдбаре
    st.session_state.manager.settings.update(st.session_state.settings)
    return st.session_state.manager


def default_settings() -> dict:
    return {
        "ocr_engine": "auto",
        "deskew": True,
        "trim_borders": True,
        "normalize_brightness": True,
        "make_review_pdf": True,
        "extract_metadata": True,
        "two_page_view": True,
        "linearize": True,
        "keep_workdir": False,
    }


if "settings" not in st.session_state:
    st.session_state.settings = default_settings()


# ── боковая панель: общие настройки партии ──────────────────────────

def sidebar() -> None:
    s = st.session_state.settings
    with st.sidebar:
        st.header("Настройки партии")

        engines = {"auto": "Авто", "easyocr": "Лёгкий",
                   "chandra": "Нейросетевой"}
        s["ocr_engine"] = st.selectbox(
            "Движок распознавания", options=list(engines),
            format_func=engines.get,
            index=list(engines).index(s["ocr_engine"]))

        st.subheader("Предобработка")
        s["deskew"] = st.checkbox("Выравнивать наклон страниц", s["deskew"])
        s["trim_borders"] = st.checkbox("Обрезать поля сканера",
                                        s["trim_borders"])
        s["normalize_brightness"] = st.checkbox(
            "Выравнивать яркость между страницами",
            s["normalize_brightness"])

        st.subheader("Результат")
        s["make_review_pdf"] = st.checkbox(
            "Создавать копию с подсветкой сомнительных слов",
            s["make_review_pdf"])
        s["extract_metadata"] = st.checkbox(
            "Определять название, автора и год с титульной страницы",
            s["extract_metadata"])
        s["keep_workdir"] = st.checkbox(
            "Сохранять рабочие файлы после успешной обработки",
            s["keep_workdir"],
            help="Не удалять временную папку с результатом предобработки "
                 "и журналом распознавания — полезно для пересборки PDF "
                 "с другими настройками без повторного распознавания.")

        st.divider()
        try:
            from stage_2.engines import (chandra_runtime_available,
                                         hardware_summary)
            hw = hardware_summary()
            cuda = "поддерживается" if hw["cuda"] else "не поддерживается"
            neural = ("установлен" if chandra_runtime_available()
                      else "не установлен")
            st.caption(f"Оперативная память: {hw['ram_gb']} ГБ. "
                       f"CUDA: {cuda}. "
                       f"Нейросетевой движок: {neural}.")
        except Exception:
            pass


# ── загрузка книги ───────────────────────────────────────────────────

def save_upload(files, zip_file) -> tuple[str, Path] | None:
    """Сохраняет загруженные файлы книги; возвращает (имя, папка)."""
    ws = get_workspace()
    ws["uploads"].mkdir(parents=True, exist_ok=True)

    if zip_file is not None:
        name = Path(zip_file.name).stem
        folder = Path(tempfile.mkdtemp(prefix=f"{name}_",
                                       dir=ws["uploads"]))
        with zipfile.ZipFile(io.BytesIO(zip_file.read())) as z:
            for info in z.infolist():
                if info.filename.lower().endswith((".tif", ".tiff")) \
                        and not info.is_dir():
                    target = folder / Path(info.filename).name
                    target.write_bytes(z.read(info))
        tiffs = list(folder.glob("*.tif*"))
        if not tiffs:
            st.error("В ZIP-архиве не найдено файлов .tif/.tiff.")
            return None
        return name, folder

    if files:
        name = st.session_state.get("book_name") or "Книга"
        folder = Path(tempfile.mkdtemp(prefix=f"{name}_",
                                       dir=ws["uploads"]))
        for f in files:
            (folder / Path(f.name).name).write_bytes(f.read())
        return name, folder
    return None


def upload_section(mgr: QueueManager) -> None:
    st.subheader("Добавить книгу")
    tab_zip, tab_files = st.tabs(["ZIP-архив со сканами",
                                  "Отдельные TIFF-файлы"])
    with tab_zip:
        zip_file = st.file_uploader(
            "ZIP с TIFF-страницами одной книги (имя архива станет "
            "названием книги)", type=["zip"], key="zip_up")
        if zip_file and st.button("Добавить из ZIP"):
            res = save_upload(None, zip_file)
            if res:
                mgr.add_book(res[0], str(res[1]))
                st.rerun()
    with tab_files:
        st.text_input("Название книги", key="book_name")
        files = st.file_uploader("TIFF-страницы", type=["tif", "tiff"],
                                 accept_multiple_files=True, key="tif_up")
        if files and st.button("Добавить книгу"):
            res = save_upload(files, None)
            if res:
                mgr.add_book(res[0], str(res[1]))
                st.rerun()


# ── карточки книг ────────────────────────────────────────────────────

def book_card(mgr: QueueManager, b) -> None:
    with st.container(border=True):
        head_l, head_r = st.columns([4, 2])
        with head_l:
            st.markdown(f"**{b.name}** — {STATUS_TITLES[b.status]}")
        with head_r:
            cols = st.columns(3)
            if b.status == ADDED:
                if cols[0].button("Начать", key=f"start_{b.id}",
                                  type="primary", width="stretch"):
                    mgr.start_book(b.id)
                    st.rerun()
            if b.status in (QUEUED, PROCESSING):
                if cols[1].button("Стоп", key=f"stop_{b.id}",
                                  width="stretch"):
                    mgr.stop_book(b.id)
                    st.rerun()
            if b.status in (STOPPED, ERROR):
                if cols[0].button("Продолжить", key=f"re_{b.id}",
                                  width="stretch"):
                    mgr.start_book(b.id)   # встанет в конец очереди
                    st.rerun()
            if b.status in (ADDED, STOPPED, DONE, ERROR):
                if cols[2].button("Закрыть", key=f"rm_{b.id}",
                                  width="stretch",
                                  help="Убрать карточку из списка "
                                       "(готовые PDF не удаляются)"):
                    mgr.remove_book(b.id)
                    st.rerun()

        # индивидуальные настройки — только до запуска
        if b.status == ADDED:
            c1, c2 = st.columns(2)
            dpi = c1.number_input("Разрешение (DPI)", 72, 600, b.dpi,
                                  key=f"dpi_{b.id}", help=DPI_HINT)
            bs = c2.number_input("Страниц на проход", 1, 16, b.batch_size,
                                 key=f"bs_{b.id}",
                                 help="Используется нейросетевым движком.")
            mgr.update_book_settings(b.id, dpi=dpi, batch_size=bs)

        # прогресс
        if b.status in (QUEUED, PROCESSING, STOPPED, DONE, ERROR):
            for stage in (1, 2, 3):
                st.progress(b.progress[stage] / 100.0,
                            text=f"{STAGE_TITLES[stage]} — "
                                 f"{b.progress[stage]}%")
        # ── время обработки книги ──
        if b.status == PROCESSING:
            t = f"Прошло: {fmt_dur(b.elapsed_sec())}"
            eta = b.eta_sec()
            t += f" · Осталось: ≈ {fmt_dur(eta)}" if eta is not None                  else " · Осталось: оценивается…"
            st.caption(t)
        elif b.status == STOPPED and b.elapsed_prev:
            st.caption(f"Прошло до остановки: {fmt_dur(b.elapsed_prev)}")
        elif b.status in (DONE, ERROR) and b.finished_at:
            st.caption(f"Затрачено: {fmt_dur(b.elapsed_prev)} · "
                       f"Завершено в {b.finished_at}")

        if b.message:
            st.caption(b.message)

        # скачивание результатов
        if b.status == DONE:
            d1, d2, d3 = st.columns(3)
            if b.output_pdf and Path(b.output_pdf).exists():
                d1.download_button(
                    "Скачать PDF", Path(b.output_pdf).read_bytes(),
                    file_name=Path(b.output_pdf).name,
                    mime="application/pdf", key=f"dl1_{b.id}",
                    width="stretch")
            if b.review_pdf and Path(b.review_pdf).exists():
                d2.download_button(
                    "PDF с подсветкой", Path(b.review_pdf).read_bytes(),
                    file_name=Path(b.review_pdf).name,
                    mime="application/pdf", key=f"dl2_{b.id}",
                    width="stretch")
            if b.report_txt and Path(b.report_txt).exists():
                d3.download_button(
                    "Отчёт (txt)", Path(b.report_txt).read_bytes(),
                    file_name=Path(b.report_txt).name,
                    mime="text/plain", key=f"dl3_{b.id}",
                    width="stretch")


@st.fragment(run_every=2.0)
def books_section(mgr: QueueManager) -> None:
    # ── общая статистика по времени ──
    stats = mgr.batch_stats()
    if stats["finished_at"] or stats["elapsed_sec"]:
        c1, c2, c3 = st.columns(3)
        if stats["finished_at"]:
            c1.metric("Прошло", "—")
            c2.metric("Осталось", "—")
            c3.metric("Обработка завершена", stats["finished_at"])
        else:
            c1.metric("Прошло", fmt_dur(stats["elapsed_sec"]))
            c2.metric("Осталось",
                      f"≈ {fmt_dur(stats['eta_sec'])}"
                      if stats["eta_sec"] is not None else "оценивается…")
            c3.metric("Обработка завершена", "ещё идёт")

    books = mgr.ordered_books()
    if not books:
        st.info("Книги ещё не добавлены. Загрузите сканы выше.")
        return
    for b in books:
        book_card(mgr, b)


# ── страница подготовки (первый старт контейнера) ────────────────────

def preparation_gate() -> None:
    """
    Пока контейнер докачивает модели, показывает страницу статуса и не
    пускает к основному интерфейсу. Активна только в Docker: entrypoint
    задаёт переменные TIFF2PDF_PREP_*. При обычном (нативном) запуске
    переменных нет — функция сразу возвращает управление.
    """
    done = os.environ.get("TIFF2PDF_PREP_DONE")
    status_file = os.environ.get("TIFF2PDF_PREP_STATUS")
    if not done or Path(done).exists():
        return

    st.title("Цифровая библиотека")
    st.info("Идёт первоначальная подготовка сервера. Скачиваются модели "
            "распознавания (~4.7 ГБ) — это может занять несколько минут. "
            "Страница обновится автоматически.")
    step = ""
    try:
        if status_file and Path(status_file).exists():
            step = Path(status_file).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if step:
        st.write(f"Текущий шаг: **{step}**")
    with st.spinner("Подготовка…"):
        time.sleep(3)
    st.rerun()


# ── страница ─────────────────────────────────────────────────────────

def main() -> None:
    preparation_gate()

    st.title("Цифровая библиотека")
    st.caption("Загрузите сканы книги — программа распознает текст и "
               "соберёт из них PDF с поиском по содержимому.")

    sidebar()
    mgr = get_manager()

    upload_section(mgr)

    a1, a2 = st.columns([1, 1])
    added = [b for b in mgr.ordered_books() if b.status == ADDED]
    if a1.button(f"▶ Начать обработку всех добавленных ({len(added)})",
                 disabled=not added, type="primary", width="stretch"):
        mgr.start_all()
        st.rerun()
    if a2.button("■ Остановить всё",
                 disabled=not mgr.is_busy(), width="stretch",
                 help="Прерывает текущую книгу (можно продолжить позже) "
                      "и снимает с очереди ожидающие"):
        mgr.stop_all()
        st.rerun()

    st.divider()
    books_section(mgr)


if __name__ == "__main__":
    main()
else:
    main()
