from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="Bonuska", version="0.1.0")

templates = Jinja2Templates(directory="app/templates")


class Profile(BaseModel):
    id: int
    bdm_name: str
    city: str
    salary: float
    bonus: float


class Project(BaseModel):
    id: int
    calculation_id: int
    project_number: str
    client: str
    object_name: str
    manager: str
    department: str


class Calculation(BaseModel):
    id: int
    profile_id: int
    month: str
    year: int
    stm_plan: float
    stm_fact: float = 0
    electrical_plan: float
    electrical_fact: float = 0
    sales_department_plan_done: bool = False
    projects: list[Project] = []


profiles: list[Profile] = []
calculations: list[Calculation] = []

next_profile_id = 1
next_calculation_id = 1
next_project_id = 1


def get_profile(profile_id: int) -> Profile | None:
    return next((p for p in profiles if p.id == profile_id), None)


def get_calculation(calculation_id: int) -> Calculation | None:
    return next((c for c in calculations if c.id == calculation_id), None)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "profiles": profiles},
    )


@app.get("/profiles/new", response_class=HTMLResponse)
async def new_profile(request: Request):
    return templates.TemplateResponse("profile_form.html", {"request": request})


@app.post("/profiles")
async def create_profile(
    bdm_name: str = Form(...),
    city: str = Form(...),
    salary: float = Form(...),
    bonus: float = Form(...),
):
    global next_profile_id

    profile = Profile(
        id=next_profile_id,
        bdm_name=bdm_name,
        city=city,
        salary=salary,
        bonus=bonus,
    )

    profiles.append(profile)
    next_profile_id += 1

    return RedirectResponse(url=f"/profiles/{profile.id}", status_code=303)


@app.get("/profiles/{profile_id}", response_class=HTMLResponse)
async def profile_detail(request: Request, profile_id: int):
    profile = get_profile(profile_id)

    if profile is None:
        return RedirectResponse(url="/", status_code=303)

    profile_calculations = [
        calculation for calculation in calculations
        if calculation.profile_id == profile.id
    ]

    return templates.TemplateResponse(
        "profile_detail.html",
        {
            "request": request,
            "profile": profile,
            "calculations": profile_calculations,
        },
    )


@app.get("/profiles/{profile_id}/calculation/new", response_class=HTMLResponse)
async def new_calculation(request: Request, profile_id: int):
    profile = get_profile(profile_id)

    if profile is None:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "calculation_form.html",
        {"request": request, "profile": profile},
    )


@app.post("/calculations")
async def create_calculation(
    profile_id: int = Form(...),
    month: str = Form(...),
    year: int = Form(...),
    stm_plan: float = Form(...),
    electrical_plan: float = Form(...),
    sales_department_plan_done: bool = Form(False),
):
    global next_calculation_id

    profile = get_profile(profile_id)

    if profile is None:
        return RedirectResponse(url="/", status_code=303)

    calculation = Calculation(
        id=next_calculation_id,
        profile_id=profile.id,
        month=month,
        year=year,
        stm_plan=stm_plan,
        electrical_plan=electrical_plan,
        sales_department_plan_done=sales_department_plan_done,
        projects=[],
    )

    calculations.append(calculation)
    next_calculation_id += 1

    return RedirectResponse(url=f"/calculations/{calculation.id}", status_code=303)


@app.get("/calculations/{calculation_id}", response_class=HTMLResponse)
async def calculation_detail(request: Request, calculation_id: int):
    calculation = get_calculation(calculation_id)

    if calculation is None:
        return RedirectResponse(url="/", status_code=303)

    profile = get_profile(calculation.profile_id)

    if profile is None:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "calculation_detail.html",
        {
            "request": request,
            "profile": profile,
            "calculation": calculation,
        },
    )


@app.get("/calculations/{calculation_id}/projects/new", response_class=HTMLResponse)
async def new_project(request: Request, calculation_id: int):
    calculation = get_calculation(calculation_id)

    if calculation is None:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "project_form.html",
        {"request": request, "calculation": calculation},
    )


@app.post("/projects")
async def create_project(
    calculation_id: int = Form(...),
    project_number: str = Form(...),
    client: str = Form(...),
    object_name: str = Form(...),
    manager: str = Form(""),
    department: str = Form(""),
):
    global next_project_id

    calculation = get_calculation(calculation_id)

    if calculation is None:
        return RedirectResponse(url="/", status_code=303)

    project = Project(
        id=next_project_id,
        calculation_id=calculation.id,
        project_number=project_number,
        client=client,
        object_name=object_name,
        manager=manager,
        department=department,
    )

    calculation.projects.append(project)
    next_project_id += 1

    return RedirectResponse(url=f"/calculations/{calculation.id}", status_code=303)