"""Generate a short PDF cheat sheet for Hexlet final review."""

from pathlib import Path

from fpdf import FPDF

FONT = Path(r"C:\Windows\Fonts\arial.ttf")
FONT_B = Path(r"C:\Windows\Fonts\arialbd.ttf")
OUT = Path(__file__).resolve().parents[1] / "docs" / "shpargalka-review.pdf"


class PDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("ArialUni", size=9)
        self.cell(0, 8, f"{self.page_no()}", align="C")


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    pdf = PDF()
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.add_font("ArialUni", "", str(FONT))
    pdf.add_font("ArialUni", "B", str(FONT_B))
    w = pdf.epw

    def h1(text: str) -> None:
        pdf.set_font("ArialUni", "B", 16)
        pdf.multi_cell(w, 9, text)
        pdf.ln(3)

    def h2(text: str) -> None:
        pdf.ln(2)
        pdf.set_font("ArialUni", "B", 12)
        pdf.multi_cell(w, 7, text)
        pdf.ln(1)

    def p(text: str) -> None:
        pdf.set_font("ArialUni", "", 11)
        pdf.multi_cell(w, 6, text)
        pdf.ln(1)

    def bullet(text: str) -> None:
        pdf.set_font("ArialUni", "", 11)
        pdf.multi_cell(w, 6, f"* {text}")

    h1("Шпаргалка: что объяснить на финальном ревью")
    p("Проект: AI-агент службы поддержки (Help Desk)")

    h2("1. Секреты и роли")
    p("Секреты — пароли и строки подключения. Их нельзя класть в GitHub.")
    p("Они лежат в Lockbox (сейф Яндекса):")
    bullet("ydb-endpoint / ydb-database — как подключиться к базе")
    bullet("пароль почты (app-password) — для IMAP/SMTP")
    p("Cloud Function сама забирает их из Lockbox при запуске.")
    p(
        "Сервисный аккаунт ai-studio-sa — «робот» с правами: вызывать функции/MCP, "
        "читать Lockbox, ходить в модель, писать в YDB. Без этих ролей функция "
        "не сможет ни в базу, ни к паролю почты."
    )

    h2("2. Почему pull, а не webhook")
    p('Pull = мы сами раз в минуту спрашиваем: «есть новые письма?» (IMAP).')
    p('Webhook = почта сама стучится к нам: «вот новое письмо».')
    p(
        "Почему pull: проще (таймер + Cloud Function), не нужен публичный вход "
        "с интернета под почту. Минус — ответ не мгновенный, а примерно до минуты. "
        "Для учебного Help Desk этого хватает."
    )

    h2("3. Почему MCP через Cloud Function")
    p(
        "MCP — способ дать агенту «кнопки» "
        "(create-ticket, list-my-tickets, append-message)."
    )
    p("Цепочка: агент → MCP gateway → наша Cloud Function → YDB.")
    p("Почему не чужой remote в интернете:")
    bullet("база и логика у нас в облаке")
    bullet("можно добавить защиту (injection, PII) до записи в базу")
    bullet("секреты YDB не светятся наружу")
    p("Итого: инструмент свой, контролируемый.")

    h2("4. Почему Responses API, а не Assistants")
    p(
        "Assistants API — старый способ (агенты/треды), его сворачивают / он устарел."
    )
    p(
        "Responses API — актуальный: один запрос → ответ + tools "
        "(file_search, MCP). Им пользуется email-poller."
    )
    p("Коротко: Assistants устарел → работаем на Responses.")

    h2("Шпаргалка одной строкой")
    p(
        "Секреты в Lockbox + роли у SA. Почту забираем по таймеру (pull). "
        "Тикеты через свою CF (MCP). API — Responses, не Assistants."
    )

    pdf.output(str(OUT))
    print(OUT)


if __name__ == "__main__":
    main()
