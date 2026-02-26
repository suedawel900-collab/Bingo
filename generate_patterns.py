import json
import os
from itertools import product

def generate_patterns():
    patterns = []
    pid = 1

    # 1. Rows (5)
    for r in range(5):
        patterns.append({
            "id": pid,
            "name": f"Row {r+1}",
            "cells": [[c, r] for c in range(5)]
        })
        pid += 1

    # 2. Columns (5)
    for c in range(5):
        patterns.append({
            "id": pid,
            "name": f"Column {c+1}",
            "cells": [[c, r] for r in range(5)]
        })
        pid += 1

    # 3. Diagonals (2)
    patterns.append({"id": pid, "name": "Main Diagonal", "cells": [[i, i] for i in range(5)]}); pid += 1
    patterns.append({"id": pid, "name": "Anti Diagonal", "cells": [[4-i, i] for i in range(5)]}); pid += 1

    # 4. All 2x2 squares (16)
    for r in range(4):
        for c in range(4):
            patterns.append({
                "id": pid,
                "name": f"2x2 Square ({c+1},{r+1})",
                "cells": [[c, r], [c+1, r], [c, r+1], [c+1, r+1]]
            })
            pid += 1

    # 5. All 3x3 squares (9)
    for r in range(3):
        for c in range(3):
            cells = [[cx, cy] for cy in range(r, r+3) for cx in range(c, c+3)]
            patterns.append({
                "id": pid,
                "name": f"3x3 Square ({c+1},{r+1})",
                "cells": cells
            })
            pid += 1

    # 6. Corners (4)
    patterns.append({"id": pid, "name": "Four Corners", "cells": [[0,0], [4,0], [0,4], [4,4]]}); pid += 1
    # 7. X shape (both diagonals together) – but that's two patterns, we already have diagonals separately.
    # Instead, add "All corners + center" pattern:
    patterns.append({"id": pid, "name": "Corners + Center", "cells": [[0,0], [4,0], [0,4], [4,4], [2,2]]}); pid += 1

    # 8. Plus signs at every possible center (if shape fits)
    plus_shape = [[0,0], [0,-1], [0,1], [-1,0], [1,0]]
    for r in range(5):
        for c in range(5):
            cells = []
            valid = True
            for dc, dr in plus_shape:
                nc, nr = c+dc, r+dr
                if 0 <= nc < 5 and 0 <= nr < 5:
                    cells.append([nc, nr])
                else:
                    valid = False
                    break
            if valid:
                patterns.append({
                    "id": pid,
                    "name": f"Plus at ({c+1},{r+1})",
                    "cells": cells
                })
                pid += 1

    # 9. Border (all edge cells)
    border = []
    for i in range(5):
        border.append([i, 0])      # top row
        border.append([i, 4])      # bottom row
        border.append([0, i])      # left column
        border.append([4, i])      # right column
    # remove duplicates (corners appear twice)
    border = list(map(list, set(tuple(c) for c in border)))
    patterns.append({"id": pid, "name": "Border", "cells": border}); pid += 1

    # 10. Inner border (one step in)
    inner = []
    for i in range(1,4):
        inner.append([i, 1])
        inner.append([i, 3])
        inner.append([1, i])
        inner.append([3, i])
    inner = list(map(list, set(tuple(c) for c in inner)))
    patterns.append({"id": pid, "name": "Inner Border", "cells": inner}); pid += 1

    # 11. Checkerboard (all black squares of chessboard)
    black = [[c, r] for r in range(5) for c in range(5) if (c+r)%2 == 0]
    patterns.append({"id": pid, "name": "Checkerboard (even sum)", "cells": black}); pid += 1
    white = [[c, r] for r in range(5) for c in range(5) if (c+r)%2 == 1]
    patterns.append({"id": pid, "name": "Checkerboard (odd sum)", "cells": white}); pid += 1

    # 12. Small letter shapes (L, T, Z, etc.)
    # L shape (top-left corner of a 3x3)
    L_shape = [[0,0], [1,0], [2,0], [0,1], [0,2]]
    patterns.append({"id": pid, "name": "L shape", "cells": L_shape}); pid += 1
    # T shape
    T_shape = [[0,0], [1,0], [2,0], [1,1], [1,2]]
    patterns.append({"id": pid, "name": "T shape", "cells": T_shape}); pid += 1
    # Z shape (3x3 zigzag)
    Z_shape = [[0,0], [1,0], [2,0], [1,1], [0,2], [1,2], [2,2]]
    patterns.append({"id": pid, "name": "Z shape", "cells": Z_shape}); pid += 1

    # 13. All 1x5 lines (we already have rows)
    # 14. Small crosses (3x3 with center and arms) – similar to plus but with longer arms? We'll generate more plus shapes with longer arms.
    long_plus = [[2,0], [2,1], [2,2], [2,3], [2,4], [0,2], [1,2], [3,2], [4,2]]
    patterns.append({"id": pid, "name": "Long plus", "cells": long_plus}); pid += 1

    # 15. Diagonal lines of length 4
    for i in range(2):
        diag4 = [[i, i], [i+1, i+1], [i+2, i+2], [i+3, i+3]]
        patterns.append({"id": pid, "name": f"Diag4 start ({i+1},{i+1})", "cells": diag4}); pid += 1
    for i in range(2):
        diag4a = [[4-i, i], [3-i, i+1], [2-i, i+2], [1-i, i+3]]
        patterns.append({"id": pid, "name": f"AntiDiag4 start ({4-i},{i})", "cells": diag4a}); pid += 1

    # 16. Random patterns (generate remaining to reach 100)
    import random
    random.seed(42)
    existing = set(frozenset(tuple(cell) for cell in p["cells"]) for p in patterns)
    while pid <= 100:
        # generate a random pattern of size 5 to 9 cells
        size = random.randint(5, 9)
        cells = []
        attempts = 0
        while len(cells) < size and attempts < 1000:
            c, r = random.randint(0,4), random.randint(0,4)
            if [c,r] not in cells:
                cells.append([c,r])
            attempts += 1
        cells.sort()
        key = frozenset(tuple(c) for c in cells)
        if key not in existing and len(cells) >= 5:
            patterns.append({"id": pid, "name": f"Pattern {pid}", "cells": cells})
            existing.add(key)
            pid += 1

    return patterns

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    patterns = generate_patterns()
    with open("static/patterns.json", "w") as f:
        json.dump(patterns, f, indent=2)
    print(f"✅ Generated {len(patterns)} patterns.")