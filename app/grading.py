"""WAEC nine-point grading scale + grade_for() used by report cards."""

DEFAULT_WAEC_BANDS = [
    {"grade": "A1", "min": 75, "max": 100, "remark": "Excellent"},
    {"grade": "B2", "min": 70, "max": 74, "remark": "Very Good"},
    {"grade": "B3", "min": 65, "max": 69, "remark": "Good"},
    {"grade": "C4", "min": 60, "max": 64, "remark": "Credit"},
    {"grade": "C5", "min": 55, "max": 59, "remark": "Credit"},
    {"grade": "C6", "min": 50, "max": 54, "remark": "Credit"},
    {"grade": "D7", "min": 45, "max": 49, "remark": "Pass"},
    {"grade": "E8", "min": 40, "max": 44, "remark": "Pass"},
    {"grade": "F9", "min": 0, "max": 39, "remark": "Fail"},
]


def grade_for(total, bands=None):
    """Return (grade, remark) for a numeric total using the band list.

    bands: iterable of dicts with keys grade/min/max/remark. Matching is done
    on the ``min`` threshold of each band (bands sorted descending by min),
    so fractional totals such as 74.99 land in B2 (70-74) rather than in a
    gap between bands. Totals above every band get the top grade; totals
    below every band get the bottom grade. Defaults to the WAEC nine-point
    scale.
    """
    bands = sorted(bands if bands is not None else DEFAULT_WAEC_BANDS,
                   key=lambda b: b["min"], reverse=True)
    t = float(total)
    for band in bands:
        if t >= band["min"]:
            return band["grade"], band["remark"]
    last = bands[-1]
    return last["grade"], last["remark"]
