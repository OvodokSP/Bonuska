from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(
    title="Bonuska",
    version="0.1.0",
)

templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request},
    )
@app.get("/calculation", response_class=HTMLResponse)
async def calculation(request: Request):
    return templates.TemplateResponse(
        "calculation_form.html",
        {"request": request},
    )