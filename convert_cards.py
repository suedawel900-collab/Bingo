import json
import re
import os

def parse_cards_from_html(html_file, output_file):
    """Parse bingo cards from HTML and save to JSON"""
    try:
        # Check if input file exists
        if not os.path.exists(html_file):
            print(f"❌ Error: Input file '{html_file}' not found!")
            return False
        
        print(f"📖 Reading cards from {html_file}...")
        
        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()
        
        print(f"✅ HTML file loaded ({len(html)} characters)")
        
        # Find all card divs
        card_pattern = r"<div class='card'><h2>Card ID: (\d+)</h2><table>(.*?)</table></div>"
        cards = []
        
        matches = re.finditer(card_pattern, html, re.DOTALL)
        match_count = 0
        
        for match in matches:
            match_count += 1
            card_id = int(match.group(1))
            table_html = match.group(2)
            
            # Parse table rows
            rows = re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL)
            
            # Skip header row (first row contains B,I,N,G,O)
            data_rows = rows[1:]  # First row is headers
            
            # Initialize card as 5 columns, each with 5 rows
            card_data = [[], [], [], [], []]  # 5 columns
            
            for row_idx, row in enumerate(data_rows):
                cells = re.findall(r"<td>(.*?)</td>", row)
                
                # Each cell corresponds to a column
                for col_idx, cell_value in enumerate(cells):
                    if cell_value == 'FREE':
                        card_data[col_idx].append('FREE')
                    else:
                        card_data[col_idx].append(int(cell_value))
            
            # Verify we have 5 columns with 5 rows each
            if len(card_data) == 5 and all(len(col) == 5 for col in card_data):
                cards.append({
                    "id": card_id,
                    "card": card_data
                })
                
                if card_id % 100 == 0:
                    print(f"  Processed card {card_id}...")
            else:
                print(f"⚠️ Warning: Card {card_id} has incorrect dimensions")
        
        print(f"✅ Found {match_count} card divs, parsed {len(cards)} valid cards")
        
        # Validate we have all cards
        expected_cards = 1000
        if len(cards) != expected_cards:
            print(f"⚠️ Warning: Expected {expected_cards} cards, but found {len(cards)}")
        
        # Save to JSON
        os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(cards, f, indent=2, ensure_ascii=False)
        
        print(f"\n✅ Successfully saved {len(cards)} cards to {output_file}")
        print(f"📁 File size: {os.path.getsize(output_file) / 1024:.1f} KB")
        
        # Show sample
        if cards:
            print(f"\n📊 Sample card (ID: {cards[0]['id']}):")
            card = cards[0]['card']
            for row in range(5):
                row_values = [str(card[col][row]).rjust(4) for col in range(5)]
                print('  ' + ' '.join(row_values))
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def validate_cards(json_file):
    """Validate the generated JSON file"""
    try:
        with open(json_file, 'r') as f:
            cards = json.load(f)
        
        print(f"\n🔍 Validating {len(cards)} cards...")
        
        # Check structure
        valid_count = 0
        for card in cards:
            if 'id' in card and 'card' in card:
                card_data = card['card']
                if len(card_data) == 5 and all(len(col) == 5 for col in card_data):
                    valid_count += 1
        
        print(f"✅ {valid_count} cards have valid structure")
        
        # Check for FREE space in center
        center_free_count = 0
        for card in cards:
            if card['card'][2][2] == 'FREE':
                center_free_count += 1
        
        print(f"✅ {center_free_count} cards have FREE space in center")
        
        # Show range of card IDs
        card_ids = [c['id'] for c in cards]
        if card_ids:
            print(f"📊 Card IDs range: {min(card_ids)} to {max(card_ids)}")
        
        return True
    except Exception as e:
        print(f"❌ Validation error: {e}")
        return False

def create_sample_preview():
    """Create a sample preview HTML to verify cards"""
    try:
        with open('static/bingo_cards.json', 'r') as f:
            cards = json.load(f)
        
        html = '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Bingo Cards Preview</title>
            <style>
                body { font-family: Arial; padding: 20px; background: #f0f0f0; }
                .card-container { display: flex; flex-wrap: wrap; gap: 20px; }
                .card { background: white; padding: 15px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                table { border-collapse: collapse; }
                td { width: 40px; height: 40px; text-align: center; border: 1px solid #ccc; }
                .free { background: #ffd700; }
            </style>
        </head>
        <body>
            <h1>Bingo Cards Preview (First 10 Cards)</h1>
            <div class="card-container">
        '''
        
        for card in cards[:10]:
            html += f'<div class="card"><h3>Card #{card["id"]}</h3><table>'
            for row in range(5):
                html += '<tr>'
                for col in range(5):
                    val = card['card'][col][row]
                    if val == 'FREE':
                        html += f'<td class="free">FREE</td>'
                    else:
                        html += f'<td>{val}</td>'
                html += '</tr>'
            html += '</table></div>'
        
        html += '''
            </div>
        </body>
        </html>
        '''
        
        with open('card_preview.html', 'w') as f:
            f.write(html)
        
        print("✅ Created preview at card_preview.html")
        
    except Exception as e:
        print(f"❌ Preview error: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("🎯 BINGO CARD CONVERTER")
    print("=" * 50)
    
    # Define file paths
    input_file = "1000_full_5x5_bingo_boards.html"
    output_file = "static/bingo_cards.json"
    
    # Check if input file exists
    if not os.path.exists(input_file):
        print(f"\n❌ ERROR: Could not find '{input_file}'")
        print("\nPlease make sure the HTML file is in the current directory.")
        print("Expected filename: 1000_full_5x5_bingo_boards.html")
    else:
        # Convert cards
        success = parse_cards_from_html(input_file, output_file)
        
        if success:
            # Validate the generated JSON
            validate_cards(output_file)
            
            # Create preview
            create_sample_preview()
            
            print("\n" + "=" * 50)
            print("✅ CONVERSION COMPLETE!")
            print("=" * 50)
            print("\nNext steps:")
            print("1. The JSON file is ready at: static/bingo_cards.json")
            print("2. Your webapp.py will automatically load these cards")
            print("3. Players can now select from 1000 unique cards!")
            print("\nTo verify, open 'card_preview.html' in your browser")
        else:
            print("\n❌ Conversion failed. Please check the error messages above.")

# Alternative: If the HTML structure is different, try this simpler version
def simple_parse():
    """Simpler parser if the above doesn't work"""
    import re
    
    with open('1000_full_5x5_bingo_boards.html', 'r') as f:
        content = f.read()
    
    # Find all card tables
    tables = re.findall(r'<table>(.*?)</table>', content, re.DOTALL)
    
    cards = []
    for idx, table in enumerate(tables, 1):
        # Skip the first table (might be header)
        if idx == 1:
            continue
            
        # Parse rows
        rows = re.findall(r'<tr>(.*?)</tr>', table, re.DOTALL)
        
        if len(rows) < 6:  # Need at least header + 5 data rows
            continue
            
        # Skip header row
        data_rows = rows[1:6]
        
        card_data = [[], [], [], [], []]
        
        for row in data_rows:
            cells = re.findall(r'<td>(.*?)</td>', row)
            for col, val in enumerate(cells):
                if val == 'FREE':
                    card_data[col].append('FREE')
                else:
                    card_data[col].append(int(val))
        
        if all(len(col) == 5 for col in card_data):
            cards.append({
                "id": idx - 1,
                "card": card_data
            })
    
    with open('static/bingo_cards.json', 'w') as f:
        json.dump(cards, f, indent=2)
    
    print(f"Saved {len(cards)} cards using simple parser")

# Uncomment to use simple parser if main parser fails
# if __name__ == "__main__":
#     simple_parse()