import sys
from ortools.sat.python import cp_model
from tests.test_scheduler import create_sample_members

members = create_sample_members(13)
num_members = len(members)
days = 7
slots = 24
houses = 2
total_demand_slots = days * slots * houses

model = cp_model.CpModel()
x = {}
for m in range(num_members):
    for d in range(days):
        for s in range(slots):
            for h in range(houses):
                x[m, d, s, h] = model.NewBoolVar(f"x_{m}_{d}_{s}_{h}")

# Coverage
for d in range(days):
    for s in range(slots):
        for h in range(houses):
            model.Add(sum(x[m, d, s, h] for m in range(num_members)) == 1)

# At most 1 house
for m in range(num_members):
    for d in range(days):
        for s in range(slots):
            model.Add(sum(x[m, d, s, h] for h in range(houses)) <= 1)

# Total slots
fair_floor = total_demand_slots // num_members # 25
fair_ceil = (total_demand_slots + num_members - 1) // num_members # 26

member_totals = {}
for m in range(num_members):
    member_totals[m] = model.NewIntVar(fair_floor, fair_ceil, f"tot_{m}")
    model.Add(member_totals[m] == sum(x[m, d, s, h] for d in range(days) for s in range(slots) for h in range(houses)))

# Min shift length = 2 slots
is_working = {}
for m in range(num_members):
    for d in range(days):
        for s in range(slots):
            is_working[m, d, s] = model.NewBoolVar(f"w_{m}_{d}_{s}")
            model.Add(is_working[m, d, s] == sum(x[m, d, s, h] for h in range(houses)))

for m in range(num_members):
    for d in range(days):
        for s in range(slots):
            start = model.NewBoolVar(f"st_{m}_{d}_{s}")
            if s == 0:
                model.Add(start >= is_working[m, d, 0])
            else:
                model.Add(start >= is_working[m, d, s] - is_working[m, d, s-1])
            if s + 1 < slots:
                model.Add(is_working[m, d, s+1] >= start)

solver = cp_model.CpSolver()
status = solver.Solve(model)
print("Basic model status:", solver.StatusName(status))
print("Wall time:", solver.WallTime())
