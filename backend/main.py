from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="Bonuska", version="0.1.0")

templates = Jinja2Templates(directory="app/templates")


class Calculation(BaseModel):
    bdm_name: str
    month: str
    year: int
    salary: float
    bonus: float

    stm_plan: float
    stm_fact: float = 0

    electrical_plan: float
    electrical_fact: float = 0

    sales_department_plan_done: bool = False


current_calculation: Calculation | None = None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/calculation", response_class=HTMLResponse)
async def calculation(request: Request):
    return templates.TemplateResponse("calculation_form.html", {"request": request})


@app.post("/calculation", response_class=HTMLResponse)
async def create_calculation(
    request: Request,
    bdm_name: str = Form(...),
    month: str = Form(...),
    year: int = Form(...),
    salary: float = Form(...),
    bonus: float = Form(...),
    stm_plan: float = Form(...),
    electrical_plan: float = Form(...),
    sales_department_plan_done: bool = Form(False),
):
    global current_calculation

    current_calculation = Calculation(
        bdm_name=bdm_name,
        month=month,
        year=year,
        salary=salary,
        bonus=bonus,
        stm_plan=stm_plan,
        electrical_plan=electrical_plan,
        sales_department_plan_done=sales_department_plan_done,
    )

    return templates.TemplateResponse(
        "calculation_detail.html",
        {"request": request, "calculation": current_calculation},
    )