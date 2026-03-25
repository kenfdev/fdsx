def parse_jsonpath(path: str) -> list[str | int]:
    """Parse a JSONPath-like string into typed segments.

    Handles dot notation (a.b) and bracket notation (a[0], a["key"]).
    Converts 'reviews[0].summary' into ['reviews', 0, 'summary'].
    """
    parts: list[str | int] = []
    current = ""
    i = 0

    while i < len(path):
        char = path[i]

        if char == ".":
            if current:
                parts.append(current)
                current = ""
        elif char == "[":
            if current:
                parts.append(current)
                current = ""
            i += 1
            bracket_content = ""
            while i < len(path) and path[i] != "]":
                bracket_content += path[i]
                i += 1
            if bracket_content:
                try:
                    parts.append(int(bracket_content))
                except ValueError:
                    parts.append(bracket_content.strip("\"'"))
        else:
            current += char

        i += 1

    if current:
        parts.append(current)

    return parts
