"""Small helper utilities."""
import random

AVATAR_COLORS = [
    "#e17076", "#7bc862", "#65aadd", "#a695e7", "#ee7aae",
    "#6ec9cb", "#faa774", "#3390ec", "#5eb5f7", "#f5a623",
]


def random_color() -> str:
    return random.choice(AVATAR_COLORS)
