# === backend/braille_converter.py ===

def braille_string_to_matrix(braille_string: str) -> list:
    """
    Convert a braille Unicode string to a 20×96 matrix for the visualizer.
    
    Format:
    - Input: String of braille Unicode characters with newlines separating rows
    - Each character represents one braille cell (2 dots wide × 4 dots tall)
    - Spaces represent empty braille cells
    - Device has max 32 columns × 4 rows of cells
    - Output: 20×96 matrix with spacing (matches frontend expectations)
    
    Braille dot numbering (8-dot braille):
        1 4
        2 5
        3 6
        7 8
    
    Unicode encoding (character - 0x2800 gives 8-bit pattern):
        Bit 0 = dot 1 (top left)
        Bit 1 = dot 2 (middle-top left)
        Bit 2 = dot 3 (middle-bottom left)
        Bit 3 = dot 4 (top right)
        Bit 4 = dot 5 (middle-top right)
        Bit 5 = dot 6 (middle-bottom right)
        Bit 6 = dot 7 (bottom left)
        Bit 7 = dot 8 (bottom right)
    """

    # Expected dimensions
    CELL_COLS = 32  # Number of braille cells horizontally
    CELL_ROWS = 4   # Number of braille cells vertically
    MATRIX_ROWS = 20
    MATRIX_COLS = 96

    # Initialize empty matrix
    matrix = [[0 for _ in range(MATRIX_COLS)] for _ in range(MATRIX_ROWS)]

    # Split by newlines to get rows
    lines = braille_string.split('\n')

    # Process each line (up to 4 rows)
    for cell_row, line in enumerate(lines[:CELL_ROWS]):
        # Pad or truncate line to 32 characters
        # Use U+2800 (empty braille cell) for padding
        line_padded = line.ljust(CELL_COLS, '\u2800')[:CELL_COLS]

        # Process each character in the line
        for cell_col, char in enumerate(line_padded):
            # Handle spaces as empty braille cells
            if char == ' ':
                char = '\u2800'

            # Get the dot pattern (subtract braille base to get 8-bit value)
            codepoint = ord(char)
            if codepoint < 0x2800 or codepoint > 0x28FF:
                # Not a braille character, treat as empty
                dots = 0
            else:
                dots = codepoint - 0x2800

            # Extract individual dots from the 8-bit pattern
            # Unicode Braille: bit 0=dot1, bit 1=dot2, bit 2=dot3, bit 3=dot4,
            #                  bit 4=dot5, bit 5=dot6, bit 6=dot7, bit 7=dot8
            dot1 = (dots >> 0) & 1  # Top left
            dot2 = (dots >> 1) & 1  # Middle-top left
            dot3 = (dots >> 2) & 1  # Middle-bottom left
            dot4 = (dots >> 3) & 1  # Top right
            dot5 = (dots >> 4) & 1  # Middle-top right
            dot6 = (dots >> 5) & 1  # Middle-bottom right
            dot7 = (dots >> 6) & 1  # Bottom left
            dot8 = (dots >> 7) & 1  # Bottom right

            # Calculate position in the 20×96 matrix
            # Each cell occupies 3 columns (2 dots + 1 space) and 5 rows (4 dots + 1 space)
            base_row = cell_row * 5
            base_col = cell_col * 3

            # Map dots to matrix positions
            # Left column (dots 1, 2, 3, 7)
            matrix[base_row + 0][base_col + 0] = dot1
            matrix[base_row + 1][base_col + 0] = dot2
            matrix[base_row + 2][base_col + 0] = dot3
            matrix[base_row + 3][base_col + 0] = dot7

            # Right column (dots 4, 5, 6, 8)
            matrix[base_row + 0][base_col + 1] = dot4
            matrix[base_row + 1][base_col + 1] = dot5
            matrix[base_row + 2][base_col + 1] = dot6
            matrix[base_row + 3][base_col + 1] = dot8

            # Column 2 and row 4 are spacing (already 0)

    return matrix
