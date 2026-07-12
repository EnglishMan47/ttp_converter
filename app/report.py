"""
report.py — отчёты по обработке.

По каждой книге рядом с готовым PDF сохраняется текстовый отчёт
<имя>_report.txt: страницы с неуверенным распознаванием, ошибки,
определённые метаданные. Служебный JSON пишется во временный рабочий
каталог книги и не попадает в папку с результатами.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def write_book_report(work_dir: Path, report: dict) -> Path:
    """Служебный JSON во временном рабочем каталоге книги."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def write_book_txt_report(output_dir: Path, report: dict) -> Path:
    """
    Человекочитаемый отчёт <имя>_report.txt рядом с готовым PDF.
    Имя совпадает с именем PDF плюс суффикс _report.
    """
    output_dir = Path(output_dir)
    name = report["book"]
    lines = [f"Отчёт по книге: {name}",
             f"Сформирован: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    status = report.get("status")
    if status == "done":
        s3 = report.get("stages", {}).get("stage3", {})
        meta = s3.get("metadata", {})
        lines.append(f"Итоговый файл: {report.get('output', '')}")
        lines.append(f"Страниц: {s3.get('pages', '?')}")
        lines.append(f"Закладок: {s3.get('bookmarks', 0)}")
        lines.append(f"Движок распознавания: {report.get('engine', '?')}")
        if meta.get("title"):
            lines.append("")
            lines.append("Метаданные:")
            lines.append(f"  название: {meta.get('title', '')}")
            if meta.get("author"):
                lines.append(f"  автор:    {meta['author']}")
            if meta.get("year"):
                lines.append(f"  год:      {meta['year']}")
        rp = s3.get("review_pages", [])
        if rp:
            lines.append("")
            lines.append(f"Страниц на ручную проверку: {len(rp)}")
            nums = ", ".join(str(p["page"]) for p in rp[:40])
            lines.append(f"  номера: {nums}")
        if s3.get("low_conf_words"):
            lines.append(f"Слов с низкой уверенностью: {s3['low_conf_words']}"
                         + (" (подсвечены в _review.pdf)"
                            if s3.get("review_pdf") else ""))
        errs = []
        for st in ("stage1", "stage2", "stage3"):
            errs += report.get("stages", {}).get(st, {}).get("page_errors", [])
        if errs:
            lines.append("")
            lines.append(f"Ошибки на страницах: {len(errs)}")
            for e in errs[:20]:
                where = e.get("page", e.get("file", "?"))
                lines.append(f"  {where}: {str(e['error'])[:100]}")
    elif status == "stopped":
        lines.append("Обработка остановлена пользователем.")
        lines.append("При повторном запуске продолжится с места остановки.")
    else:
        lines.append("Обработка завершилась с ошибкой:")
        lines.append(str(report.get("error", "")).splitlines()[0][:200]
                     if report.get("error") else "неизвестная ошибка")

    path = output_dir / f"{name}_report.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_batch_report(reports: list[dict]) -> str:
    """Сводный текстовый отчёт из списка отчётов по книгам."""
    lines = ["Отчёт пакетной обработки",
             f"Сформирован: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]

    if not reports:
        lines.append("Отчёты по книгам не найдены.")
        return "\n".join(lines)

    ok = [r for r in reports if r.get("status") == "done"]
    bad = [r for r in reports if r.get("status") != "done"]
    total_pages = sum(r.get("stages", {}).get("stage3", {}).get("pages", 0)
                      for r in ok)

    lines += [f"Книг обработано: {len(ok)} из {len(reports)}, "
              f"страниц: {total_pages}", ""]

    need_review = []
    for r in ok:
        s3 = r.get("stages", {}).get("stage3", {})
        rp = s3.get("review_pages", [])
        errs = (s3.get("page_errors", [])
                + r.get("stages", {}).get("stage2", {}).get("page_errors", [])
                + r.get("stages", {}).get("stage1", {}).get("page_errors", []))
        if rp or errs or s3.get("fallback_used"):
            need_review.append((r, rp, errs, s3))

    lines.append("Требуют ручной проверки:")
    if not need_review:
        lines.append("Нет — все страницы прошли с достаточной уверенностью.")
    for r, rp, errs, s3 in need_review:
        lines.append(r["book"] + ":")
        meta = s3.get("metadata", {})
        if meta.get("title"):
            lines.append(f"- метаданные: «{meta['title']}»"
                         + (f", {meta['author']}" if meta.get("author") else "")
                         + (f", {meta['year']}" if meta.get("year") else ""))
        if rp:
            pg = ", ".join(f"{p['page']} (agreement {p['agreement']}, "
                           f"{p['verdict']})" for p in rp[:20])
            more = f" … и ещё {len(rp)-20}" if len(rp) > 20 else ""
            lines.append(f"- страницы с расхождением движков: {pg}{more}")
        if s3.get("fallback_used"):
            lines.append(f"- страниц со слоем из EasyOCR (Chandra провалилась): "
                         f"{s3['fallback_used']}")
        if s3.get("low_conf_words"):
            lines.append(f"- слов с низкой уверенностью: "
                         f"{s3['low_conf_words']}"
                         + (f" — подсвечены в {Path(s3['review_pdf']).name}"
                            if s3.get("review_pdf") else ""))
        if errs:
            for e in errs[:10]:
                where = e.get("page", e.get("file", "?"))
                lines.append(f"- ошибка ({where}): {str(e['error'])[:120]}")
        lines.append("")

    if bad:
        lines.append("Книги с ошибками (можно перезапустить — продолжится с места остановки):")
        for r in bad:
            lines.append(f"- {r['book']}: "
                         f"{str(r.get('error', '')).splitlines()[0][:160]}")
        lines.append("")

    lines.append("Все книги:")
    for r in reports:
        s3 = r.get("stages", {}).get("stage3", {})
        status = "OK" if r.get("status") == "done" else "ОШИБКА"
        lines.append(f"- [{status}] {r['book']}: "
                     f"{s3.get('pages', '?')} стр., "
                     f"закладок {s3.get('bookmarks', 0)}, "
                     f"{r.get('elapsed_sec', '?')} с")
    return "\n".join(lines)
