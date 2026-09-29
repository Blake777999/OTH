import time
from ortools.sat.python import cp_model
from tests.test_scheduler import create_sample_members
from backend.scheduler import ScheduleOptimizer

members = create_sample_members(13)
opt = ScheduleOptimizer(members, [], [], time_limit_seconds=15.0)
t0 = time.time()
res = opt.solve()
print("Elapsed:", time.time() - t0)
print("Status:", res["status"])
print("Warnings:", res["warnings"])
