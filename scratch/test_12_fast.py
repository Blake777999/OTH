import time
from ortools.sat.python import cp_model
from tests.test_scheduler import create_sample_members

members = create_sample_members(12)
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

is_working = {}
for m in range(num_members):
    for d in range(days):
        for s in range(slots):
            is_working[m, d, s] = model.NewBoolVar(f"w_{m}_{d}_{s}")
            model.Add(is_working[m, d, s] == sum(x[m, d, s, h] for h in range(houses)))

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

# Equal hours bounds: 28 slots each
for m in range(num_members):
    tot = sum(x[m, d, s, h] for d in range(days) for s in range(slots) for h in range(houses))
    model.Add(tot == 28)

# Anti-fragmentation
for m in range(num_members):
    for d in range(days):
        model.Add(is_working[m, d, 0] <= is_working[m, d, 1])
        model.Add(is_working[m, d, slots - 1] <= is_working[m, d, slots - 2])
        for s in range(1, slots - 1):
            model.Add(is_working[m, d, s] <= is_working[m, d, s - 1] + is_working[m, d, s + 1])

obj_terms = []

# House switching penalty
for m in range(num_members):
    for d in range(days):
        for s in range(1, slots):
            sw = model.NewBoolVar(f"sw_{m}_{d}_{s}")
            model.Add(sw >= x[m, d, s-1, 0] + x[m, d, s, 1] - 1)
            model.Add(sw >= x[m, d, s-1, 1] + x[m, d, s, 0] - 1)
            obj_terms.append(-20 * sw)

# Preferences
for m_idx, m in enumerate(members):
    for d in range(days):
        day_slots = sum(x[m_idx, d, s, h] for s in range(slots) for h in range(houses))
        works_day = model.NewBoolVar(f"wd_{m_idx}_{d}")
        model.Add(day_slots <= 12)
        model.Add(day_slots <= slots * works_day)

        if m.shift_preference == "daily_short":
            obj_terms.append(15 * works_day)
            excess = model.NewIntVar(0, slots, f"ex_{m_idx}_{d}")
            model.Add(excess >= day_slots - 4)
            obj_terms.append(-10 * excess)
        elif m.shift_preference == "fewer_long":
            obj_terms.append(-15 * works_day)

model.Maximize(sum(obj_terms))

solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 10.0
solver.parameters.num_workers = 4
t0 = time.time()
status = solver.Solve(model)
print("Solve status:", solver.StatusName(status))
print("Solve time:", time.time() - t0)
