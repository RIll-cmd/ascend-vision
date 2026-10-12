"""One question during setup: an AI key. Everything else works without it."""
from pathlib import Path

ENV = Path(__file__).with_name('.env')


def main() -> int:
    if ENV.is_file() and 'GEMINI_API_KEY=' in ENV.read_text(encoding='utf-8'):
        print('AI key already saved in .env - leaving it alone.')
        return 0
    print()
    print('Vision talks and roasts using a free Gemini key from https://aistudio.google.com/apikey')
    print('Press Enter to skip; Vision still works with simple built-in replies.')
    key = input('Paste your Gemini key (it stays in .env on this computer): ').strip()
    if not key:
        print('Skipped. Add GEMINI_API_KEY=... to .env any time.')
        return 0
    existing = ENV.read_text(encoding='utf-8') if ENV.is_file() else ''
    separator = '' if not existing or existing.endswith('\n') else '\n'
    ENV.write_text(f'{existing}{separator}GEMINI_API_KEY={key}\n', encoding='utf-8')
    print('Saved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
