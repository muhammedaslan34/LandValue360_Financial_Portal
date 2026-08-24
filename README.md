# LandValue360 Financial Portal v2.5.0

Standalone FastAPI financial portal using the LandValue360 2.1.1 monthly engine. Standard users receive simple project inputs and complete project/developer/landowner feasibility outputs. Administrators manage immutable, selectable financial policy versions containing all timing, financing, collection, cost, liquidity and negotiation assumptions.

v2.5.0 introduces a minimum-anchored negotiation chart, collision-free labels, evidence-based explanations for every negotiation boundary, a formal 11-page PDF report, cleaned policy-number inputs and removal of the legacy analyst-status workspace from the simple portal workflow.

Run locally:

```bash
./START_PORTAL.sh
```

Windows:

```powershell
.\START_PORTAL.bat
```

Before deployment:

```bash
python scripts/verify_release.py
python scripts/runtime_preflight.py
alembic upgrade head
```
