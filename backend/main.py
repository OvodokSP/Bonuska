from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="Bonuska",
    version="0.1.0"
)


@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <title>Бонуска</title>
    </head>
    <body style="font-family: Arial; margin:40px;">
        <h1>Бонуска</h1>

        <h3>Автоматизация расчета бонусной ведомости</h3>

        <hr>

        <button>Создать расчет</button>
        <button>Добавить счет</button>

        <hr>

        <p>Пока счетов нет.</p>

    </body>
    </html>
    """