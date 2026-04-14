"""
PNG Background Remover
Entry point — kept minimal for clean PyInstaller analysis.
"""

from app import BgRemoverApp


def main():
    app = BgRemoverApp()
    app.mainloop()


if __name__ == '__main__':
    main()
