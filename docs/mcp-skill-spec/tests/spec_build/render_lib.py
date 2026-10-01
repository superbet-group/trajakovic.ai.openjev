import json, math
def rnd(x):
    if isinstance(x, float):
        if x == 0 or abs(x) >= 0.001: return round(x, 4)
        return float(f"{x:.2g}")
    if isinstance(x, dict): return {k: rnd(v) for k, v in x.items()}
    if isinstance(x, list): return [rnd(v) for v in x]
    return x
def compact(obj):
    """JSON with one question/answer per line."""
    return json.dumps(obj, ensure_ascii=False, indent=1)
