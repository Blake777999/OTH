import sys
from ortools.sat.python import cp_model
from backend.models import Member, MasterScheduleItem, WeeklyOverrideItem
from backend.scheduler import ScheduleOptimizer

def test():
    names = ["Dylan", "Nate", "Simon", "Blake Cohen", "Jacob", "Oliver", "Kobi", "Ethan", "Sebastian", "Corey", "Alex", "Blake Rosen"]
    members = [Member(id=f"m_{i}", name=names[i], active=True, weight=1.0, shift_preference="daily_short" if i % 2 == 0 else "fewer_long") for i in range(12)]
    
    opt = ScheduleOptimizer(members, [], [], min_shift_slots=2)
    res = opt.solve()
    print("Success:", res["success"])
    print("Fairness score:", res["stats"]["fairness_score"])
    print("Hours per member:", [m["assigned_hours"] for m in res["stats"]["members"]])

if __name__ == "__main__":
    test()
