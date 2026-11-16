"""Rich display utilities for beautiful CLI output."""

from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich import box
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    SpinnerColumn,
)

from config import Config
from models import MangaInfo, Chapter
# Custom theme with vibrant colors
CUSTOM_THEME = Theme({
    "title": "bold magenta",
    "subtitle": "dim cyan",
    "highlight": "bold yellow",
    "success": "bold green",
    "error": "bold red",
    "warning": "bold orange3",
    "info": "cyan",
    "chapter": "green",
    "setting": "bold blue",
    "value": "yellow",
})

console = Console(theme=CUSTOM_THEME)


def display_banner() -> None:
    """Display the stylized application banner."""
    banner = """
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║     █████╗ ████████╗███████╗██╗   ██╗                        ║
║    ██╔══██╗╚══██╔══╝██╔════╝██║   ██║                        ║
║    ███████║   ██║   ███████╗██║   ██║                        ║
║    ██╔══██║   ██║   ╚════██║██║   ██║                        ║
║    ██║  ██║   ██║   ███████║╚██████╔╝                        ║
║    ╚═╝  ╚═╝   ╚═╝   ╚══════╝ ╚═════╝                         ║
║                                                               ║
║          ╔══════════════════════════════════╗                ║
║          ║   D O W N L O A D E R   v1.0    ║                ║
║          ╚══════════════════════════════════╝                ║
║                                                               ║
║    ▸ Download manga from atsu.moe                            ║
║    ▸ Multiple formats: Images, PDF, CBZ                      ║
║    ▸ Concurrent downloads for speed                          ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
"""
    console.print(Text(banner, style="bold cyan"))


def display_main_menu() -> None:
    """Display the main menu options."""
    menu = Table(show_header=False, box=None, padding=(0, 4))
    menu.add_column("Option", style="bold magenta", width=4)
    menu.add_column("Description", style="white")
    
    menu.add_row("1", "📥  Download Manga by URL")
    menu.add_row("2", "⚙️   Settings")
    menu.add_row("3", "🚪  Exit")
    
    console.print()
    console.print(Panel(menu, title="[title]Main Menu[/title]", border_style="cyan"))
    console.print()


def display_manga_info(manga: MangaInfo) -> None:
    """Display manga information in a beautiful panel."""
    info_text = Text()
    info_text.append("📖 Title: ", style="setting")
    info_text.append(f"{manga.title}\n", style="value")
    info_text.append("📁 Type: ", style="setting")
    info_text.append(f"{manga.manga_type}\n", style="value")
    info_text.append("📚 Chapters: ", style="setting")
    info_text.append(f"{len(manga.chapters)}", style="value")
    
    console.print()
    console.print(Panel(
        info_text,
        title="[title]Manga Information[/title]",
        border_style="green"
    ))


def display_chapters(
    chapters: List[Chapter],
    limit: int = 0,
    show_index: bool = True
) -> None:
    """Display chapter list in a formatted table.
    
    Args:
        chapters: List of chapters
        limit: Max chapters to show (0 = all)
        show_index: Whether to show chapter selection index
    """
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="dim"
    )
    
    if show_index:
        table.add_column("#", style="dim", width=6, justify="right")
    table.add_column("Chapter", style="chapter", width=30)
    table.add_column("Title", style="white")
    table.add_column("Pages", style="cyan", justify="right", width=8)
    
    display_chapters_list = chapters if limit == 0 else chapters[:limit]
    
    for i, chapter in enumerate(display_chapters_list, 1):
        if show_index:
            table.add_row(
                str(i),
                f"Ch. {chapter.number}",
                chapter.title,
                str(chapter.page_count)
            )
        else:
            table.add_row(
                f"Ch. {chapter.number}",
                chapter.title,
                str(chapter.page_count)
            )
    
    if limit > 0 and len(chapters) > limit:
        remaining = len(chapters) - limit
        table.add_row("...", f"... and {remaining} more chapters ...", "", "")
    
    console.print()
    console.print(Panel(
        table,
        title=f"[title]Chapters ({len(chapters)} total)[/title]",
        border_style="cyan"
    ))


def display_settings(config: Config) -> None:
    """Display current settings in a formatted panel."""
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Setting", style="setting", width=30)
    table.add_column("Value", style="value")
    
    table.add_row("1. Download Format", config.download_format.upper())
    table.add_row("2. Keep Images After Conversion", "Yes" if config.keep_images else "No")
    table.add_row("3. Download Path", config.download_path)
    table.add_row("4. Concurrent Chapters", str(config.concurrent_chapters))
    table.add_row("5. Concurrent Images", str(config.concurrent_images))
    table.add_row("6. Max Display Chapters", str(config.max_display_chapters) if config.max_display_chapters > 0 else "All")
    table.add_row("7. Max Retries", str(config.max_retries))
    table.add_row("8. Retry Delay (seconds)", str(config.retry_delay))
    table.add_row("9. Enable Detailed Logs", "Yes" if config.enable_logs else "No")
    table.add_row("0. Back to Main Menu", "")
    
    console.print()
    console.print(Panel(
        table,
        title="[title]⚙️  Settings[/title]",
        border_style="blue"
    ))


def display_success(message: str) -> None:
    """Display a success message."""
    console.print(f"[success]✓ {message}[/success]")


def display_error(message: str) -> None:
    """Display an error message."""
    console.print(f"[error]✗ {message}[/error]")


def display_warning(message: str) -> None:
    """Display a warning message."""
    console.print(f"[warning]⚠ {message}[/warning]")


def display_info(message: str) -> None:
    """Display an info message."""
    console.print(f"[info]ℹ {message}[/info]")


def create_download_progress() -> Progress:
    """Create a Rich progress bar for downloads."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        #TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def display_download_results(
    successful: int,
    failed: int,
    output_path: str,
    partial: int = 0,
    retry_manifest_path: Optional[str] = None
) -> None:
    """Display download completion summary.

    Args:
        successful: Chapters downloaded completely.
        failed: Chapters that failed entirely (or had no pages).
        output_path: Directory the manga was saved to.
        partial: Chapters saved with missing images.
        retry_manifest_path: Path to the generated retry manifest, if any.
    """
    result_text = Text()
    result_text.append("\n📊 Download Summary\n", style="bold magenta")
    result_text.append("─" * 40 + "\n", style="dim")
    result_text.append("✓ Successful: ", style="success")
    result_text.append(f"{successful}\n", style="value")

    if partial > 0:
        result_text.append("⚠ Partial (missing images): ", style="warning")
        result_text.append(f"{partial}\n", style="value")

    if failed > 0:
        result_text.append("✗ Failed: ", style="error")
        result_text.append(f"{failed}\n", style="value")

    result_text.append("📁 Saved to: ", style="info")
    result_text.append(f"{output_path}\n", style="value")

    if retry_manifest_path:
        result_text.append("🔁 Retry manifest: ", style="warning")
        result_text.append(f"{retry_manifest_path}", style="value")

    console.print(Panel(
        result_text,
        border_style="green" if failed == 0 and partial == 0 else "yellow"
    ))
