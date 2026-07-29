"""Add # noqa: S608 comments to remaining lines in pgvector_backend.py."""
import re

path = r'D:\programming\langchain\langchain_xm\backend\Django_xm\Django_xm\apps\knowledge\vector_store\pgvector_backend.py'
with open(path, encoding='utf-8') as f:
    lines = f.readlines()

noqa = '  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()'

# Patterns to match (exact line content without newline)
# Each entry: (exact_line_content_without_trailing_newline, )
targets = [
    '                    f"SELECT name FROM {_quote_identifier(collection_table)} WHERE name LIKE %s",',
    '                cursor.execute(f"SELECT name FROM {_quote_identifier(collection_table)}")',
    '                f"SELECT EXISTS(SELECT 1 FROM {_quote_identifier(collection_table)} WHERE name = %s)",',
    '                f"DELETE FROM {_quote_identifier(embedding_table)} "',
    '                f"SELECT document, cmetadata FROM {_quote_identifier(embedding_table)} "',
    '                    f"SELECT COUNT(*) FROM {_quote_identifier(embedding_table)} WHERE collection_id = %s",',
]

modified = 0
for i, line in enumerate(lines):
    stripped = line.rstrip('\n')
    # Check if this line already has noqa
    if 'noqa: S608' in stripped:
        continue
    for target in targets:
        if stripped == target:
            lines[i] = stripped + noqa + '\n'
            modified += 1
            print(f'  Modified line {i+1}: {stripped[:60]}...')
            break

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f'Total modified: {modified} lines')
