import unicodedata


def _is_refused(char: str) -> bool:
    return char not in {"\n", "\r", "\t"} and unicodedata.category(char).startswith("C")


def normalize_note(text: str) -> str:
    """Squeezes the spaces; raises ValueError for control and format characters.

    People read these notes later next to other data: invisible characters (bidi overrides,
    zero-width) could make one read differently there, and NUL is refused by Postgres.
    """
    # Line breaks and tabs from a multiline field are spaces like any other.
    if any(_is_refused(char) for char in text):
        raise ValueError("the text must not contain control or format characters")
    return " ".join(text.split())
