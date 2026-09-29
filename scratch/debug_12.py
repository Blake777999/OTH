from tests.test_scheduler import create_sample_members
from backend.scheduler import ScheduleOptimizer
from ortools.sat.python import cp_model

members = create_sample_members(12)
opt = ScheduleOptimizer(members, [], [], min_shift_slots=2)
# Let's inspect solve status directly
avail = opt._build_availability_matrix()
print("Total members:", len(members))
res = opt.solve()
print("Success:", res["success"])
print("Status:", res["status"])
print("Warnings:", res.get("warnings"))
