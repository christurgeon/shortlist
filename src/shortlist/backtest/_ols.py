"""Stdlib ordinary least squares, used by the residual-momentum leg."""
from __future__ import annotations


def ols(y: list[float], X: list[list[float]]) -> list[float]:
    """Ordinary least squares via normal equations (X'X b = X'y) solved by Gaussian
    elimination. An intercept column of 1s is prepended internally. Stdlib only.
    Returns [intercept, *coeffs]. Raises ValueError on a singular system.

    NO regularization: the pivot check (abs(pivot) < 1e-12 -> raise) is load-bearing for
    the abstention contract. A materially-collinear / near-singular design must RAISE so
    the caller catches it and returns None, rather than a ridge silently returning a
    garbage alpha for an ill-conditioned regression.
    """
    n = len(y)
    if n == 0 or n != len(X):
        raise ValueError("ols: empty or mismatched input")
    k = len(X[0]) + 1
    A = [[1.0] + list(row) for row in X]                 # design matrix with intercept
    # Normal equations
    XtX = [[sum(A[r][i] * A[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    Xty = [sum(A[r][i] * y[r] for r in range(n)) for i in range(k)]
    # Gaussian elimination with partial pivoting
    M = [XtX[i] + [Xty[i]] for i in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("ols: singular normal-equations matrix")
        M[col], M[piv] = M[piv], M[col]
        pivval = M[col][col]
        M[col] = [v / pivval for v in M[col]]
        for r in range(k):
            if r != col and abs(M[r][col]) > 0:
                factor = M[r][col]
                M[r] = [a - factor * b for a, b in zip(M[r], M[col], strict=False)]
    return [M[i][k] for i in range(k)]


def _residuals(y, X, b):
    out = []
    for i in range(len(y)):
        pred = b[0] + sum(b[1 + j] * X[i][j] for j in range(len(X[i])))
        out.append(y[i] - pred)
    return out
