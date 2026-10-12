"""Two optional questions during setup: an AI key, and web use. Both can be skipped."""
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).parent
ENV = HERE / '.env'
CONFIG = HERE / 'config.yaml'
CHROME_PATHS = (
    Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
    Path('C:/Program Files (x86)/Google/Chrome/Application/chrome.exe'),
)


def enable_browser(yaml_text: str) -> str:
    """Turn on the browser_automation block only; every other `enabled:` stays as is."""
    block = re.search(r'(?m)^browser_automation:\s*\n(?P<body>(?:[ \t]+.*\n?|\n)*)', yaml_text)
    if block is None:
        raise ValueError('browser_automation section not found in config.yaml')
    body = block.group('body')
    new_body, count = re.subn(r'(?m)^([ \t]+enabled:\s*)false\b', r'\1true', body, count=1)
    if count == 0 and not re.search(r'(?m)^[ \t]+enabled:\s*true\b', body):
        raise ValueError('browser_automation.enabled not found in config.yaml')
    return yaml_text[:block.start('body')] + new_body + yaml_text[block.end('body'):]


def ask_key() -> None:
    if ENV.is_file() and 'GEMINI_API_KEY=' in ENV.read_text(encoding='utf-8'):
        print('AI key already saved in .env - leaving it alone.')
        return
    print()
    print('Vision talks and roasts using a free Gemini key from https://aistudio.google.com/apikey')
    print('Press Enter to skip; Vision still works with simple built-in replies.')
    key = input('Paste your Gemini key (it stays in .env on this computer): ').strip()
    if not key:
        print('Skipped. Add GEMINI_API_KEY=... to .env any time.')
        return
    existing = ENV.read_text(encoding='utf-8') if ENV.is_file() else ''
    separator = '' if not existing or existing.endswith('\n') else '\n'
    ENV.write_text(f'{existing}{separator}GEMINI_API_KEY={key}\n', encoding='utf-8')
    print('Saved.')


def ask_web() -> None:
    print()
    print('Vision can use the web for you in its own Google Chrome window:')
    print('look things up, and ask your OK before it clicks or types anything.')
    if input('Turn this on? (y/N): ').strip().lower() not in ('y', 'yes'):
        print('Skipped. Re-run Setup.bat any time to turn it on.')
        return
    if not any(path.is_file() for path in CHROME_PATHS):
        print('Google Chrome was not found. Install it from https://www.google.com/chrome/ and run Setup.bat again.')
        return
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '-r',
                             str(HERE / 'requirements-browser.txt')])
    if result.returncode != 0:
        print('Could not install the web tools. The message above says why; Setup can be re-run.')
        return
    CONFIG.write_text(enable_browser(CONFIG.read_text(encoding='utf-8')), encoding='utf-8')
    print('Web use is on. Say "use the browser to ..." in Vision chat.')


def main() -> int:
    ask_key()
    ask_web()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
