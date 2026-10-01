"""Flow implementations. Each flow is a callable(run, cfg) that drives the
portal and fills run.result.
"""
from backend.flows.flow1 import run_flow1
from backend.flows.flow2 import run_flow2
from backend.flows.flow3 import run_flow3
from backend.flows.flow4 import run_flow4

REGISTRY = {
    "flow1_state_monthwise": run_flow1,
    "flow2_state_fuelwise": run_flow2,
    "flow3_rto_monthwise": run_flow3,
    "flow4_rto_fuelwise": run_flow4,
}
