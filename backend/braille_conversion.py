# === backend/braille_conversion.py ===

def braille_string_to_matrix(braille_string: str) -> list:
    """
    Convert a braille Unicode string to a 20x96 matrix for the visualizer.
    
    Format:
    - Input: String of braille Unicode characters (U+2800 to U+28FF)
    - Each character represents one braille cell (2 dots wide x 4 dots tall)
    - Device has 32 columns x 4 rows of cells = 128 characters
    - Output: 20x96 matrix with spacing (matches frontend expectations)
    
    Braille dot numbering (8-dot braille):
        1 4
        2 5
        3 6
        7 8
    
    Unicode encoding (character - 0x2800 gives 8-bit pattern):
        Bit 0 = dot 1 (top left)
        Bit 1 = dot 2
        Bit 2 = dot 3
        Bit 3 = dot 7 (bottom left)
        Bit 4 = dot 4 (top right)
        Bit 5 = dot 5
        Bit 6 = dot 6
        Bit 7 = dot 8 (bottom right)
    """
    
    # Expected dimensions
    CELL_COLS = 32  # Number of braille cells horizontally
    CELL_ROWS = 4   # Number of braille cells vertically
    MATRIX_ROWS = 20
    MATRIX_COLS = 96
    
    # Initialize empty matrix
    matrix = [[0 for _ in range(MATRIX_COLS)] for _ in range(MATRIX_ROWS)]
    
    # Check string length
    expected_length = CELL_COLS * CELL_ROWS
    if len(braille_string) != expected_length:
        print(f"[WARNING] Expected {expected_length} braille characters, got {len(braille_string)}")
        # Pad or truncate as needed
        braille_string = braille_string.ljust(expected_length, '\u2800')[:expected_length]
    
    # Process each braille character
    for idx, char in enumerate(braille_string):
        # Calculate cell position
        cell_row = idx // CELL_COLS  # Which row of cells (0-3)
        cell_col = idx % CELL_COLS   # Which column of cells (0-31)
        
        # Get the dot pattern (subtract braille base to get 8-bit value)
        codepoint = ord(char)
        if codepoint < 0x2800 or codepoint > 0x28FF:
            # Not a braille character, skip
            continue
        
        dots = codepoint - 0x2800
        
        # Extract individual dots from the 8-bit pattern
        # Mapping: bits to physical dot positions
        dot1 = (dots >> 0) & 1  # Top left
        dot2 = (dots >> 1) & 1  # Middle-top left
        dot3 = (dots >> 2) & 1  # Middle-bottom left
        dot7 = (dots >> 3) & 1  # Bottom left
        dot4 = (dots >> 4) & 1  # Top right
        dot5 = (dots >> 5) & 1  # Middle-top right
        dot6 = (dots >> 6) & 1  # Middle-bottom right
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