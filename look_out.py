# github_activity.py (Corrected - border_title removed from Log init)

import os
import requests
from datetime import datetime
from dotenv import load_dotenv
import traceback

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, Log, LoadingIndicator
from textual.reactive import reactive

# Load environment variables from .env file
load_dotenv()

GITHUB_USER = os.getenv("GITHUB_USER")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# --- Check if variables are loaded ---
if not GITHUB_USER or not GITHUB_TOKEN:
    print("Error: GITHUB_USER or GITHUB_TOKEN not found in environment or .env file.")
    print("Please create a .env file in the current directory with:")
    print("GITHUB_USER=\"your-username\"")
    print("GITHUB_TOKEN=\"your-personal-access-token\"")
    exit(1)

API_URL = f"https://api.github.com/users/{GITHUB_USER}/events/public"
HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "Authorization": f"token {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28"
}

class GitHubActivityLog(Log):
    """A custom Log widget for displaying activity."""
    pass

class GitHubActivityApp(App):
    """A Textual app to view GitHub activity."""

    CSS_PATH = "github_activity.css"
    BINDINGS = [
        ("r", "refresh", "Refresh Activity"),
        ("q", "quit", "Quit"),
    ]

    loading = reactive(False)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Container(id="activity-container"):
            yield LoadingIndicator(id="loading")
            # --- CORRECTED LINE: No border_title argument ---
            yield GitHubActivityLog(
                highlight=True,
                id="log"
            )
            # --- END CORRECTION ---
        yield Footer()

    def on_mount(self) -> None:
        """Called when the app is first mounted."""
        self.run_fetch_worker()

    def watch_loading(self, loading: bool) -> None:
        """Called when the 'loading' reactive variable changes."""
        try:
            loading_indicator = self.query_one(LoadingIndicator)
            loading_indicator.styles.display = "block" if loading else "none"
        except Exception as e:
            print(f"Error updating loading indicator: {e}")
            try:
                log = self.query_one(GitHubActivityLog)
                log.write_line(f"[bold red]Error updating loading indicator: {e}[/]")
            except:
                pass

    def run_fetch_worker(self) -> None:
        """Starts the background worker to fetch activity."""
        try:
            log = self.query_one(GitHubActivityLog)
            if self.loading:
                log.write_line("[yellow]Already refreshing...[/]")
                return
            self.loading = True
            log.clear()
            self.run_worker(self.fetch_activity_data, exclusive=True, thread=True)
        except Exception as e:
             print(f"Error starting fetch worker (Log widget ready?): {e}")
             self.loading = False

    def fetch_activity_data(self) -> None:
        """ Fetches GitHub activity data from the API. Runs in worker thread. """
        log_widget = self.query_one(GitHubActivityLog)

        try:
            response = requests.get(API_URL, headers=HEADERS, timeout=15)
            response.raise_for_status()

            events = response.json()
            if not events:
                self.call_from_thread(log_widget.write_line, "[yellow]No recent public activity found.[/]")
            else:
                lines_to_write = []
                for event in events:
                    lines_to_write.append(self.format_event(event))
                self.call_from_thread(log_widget.write_lines, lines_to_write)

        except requests.exceptions.Timeout:
             self.call_from_thread(log_widget.write_line, "[bold red]Error: Request timed out.[/]")
        except requests.exceptions.HTTPError as e:
             error_message = f"[bold red]API Error: {e.response.status_code}[/]\n"
             try:
                 error_details = e.response.json()
                 error_message += f"[red]Message: {error_details.get('message', 'No details')}[/]\n"
                 docs_url = error_details.get('documentation_url')
                 if docs_url:
                     error_message += f"[dim]Docs: {docs_url}[/]"
             except ValueError:
                  error_message += f"[red]Response: {e.response.text[:200]}[/]"
             self.call_from_thread(log_widget.write_line, error_message)
        except requests.exceptions.RequestException as e:
             self.call_from_thread(log_widget.write_line, f"[bold red]Network Error: {e}[/]")
        except Exception as e:
             tb_str = traceback.format_exc()
             self.call_from_thread(log_widget.write_line, f"[bold red]An unexpected error occurred in worker:\n{e}\n{tb_str}[/]")
        finally:
             self.call_from_thread(setattr, self, 'loading', False)

    def format_event(self, event: dict) -> str:
        """Formats a single GitHub event dictionary into a readable string."""
        event_type = event.get("type", "UnknownEvent")
        repo_name = event.get("repo", {}).get("name", "[no repo]")
        created_at_str = event.get("created_at", "")
        timestamp = ""
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                local_dt = dt.astimezone()
                timestamp = f"[dim]{local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}[/dim]"
            except ValueError:
                timestamp = f"[dim]{created_at_str}[/dim]"

        payload = event.get("payload", {})
        details = ""

        # (Event formatting logic remains the same)
        if event_type == "PushEvent":
            branch = payload.get('ref', 'N/A').split('/')[-1]
            size = payload.get('size', 0)
            plural = "s" if size != 1 else ""
            details = f"Pushed {size} commit{plural} to [blue]{branch}[/]"
        elif event_type == "CreateEvent":
            ref_type = payload.get("ref_type", "N/A")
            ref = payload.get("ref")
            if ref_type == "repository":
                details = f"Created repository"
            elif ref_type == "branch" and ref:
                details = f"Created branch [blue]{ref}[/]"
            elif ref_type == "tag" and ref:
                details = f"Created tag [blue]{ref}[/]"
            else:
                details = f"Created {ref_type}"
        elif event_type == "DeleteEvent":
             ref_type = payload.get("ref_type", "N/A")
             ref = payload.get("ref", "N/A")
             details = f"Deleted {ref_type} [blue]{ref}[/]"
        elif event_type == "IssuesEvent":
            action = payload.get("action", "N/A")
            issue = payload.get("issue", {})
            issue_num = issue.get("number", "N/A")
            issue_title = issue.get("title", "")
            details = f"{action.capitalize()} issue [green]#{issue_num}[/] [i]'{issue_title}'[/i]"
        elif event_type == "IssueCommentEvent":
            action = payload.get("action", "N/A")
            issue_num = payload.get("issue", {}).get("number", "N/A")
            details = f"{action.capitalize()} comment on issue [green]#{issue_num}[/]"
        elif event_type == "PullRequestEvent":
            action = payload.get("action", "N/A")
            pr = payload.get("pull_request", {})
            pr_num = payload.get("number", pr.get("number", "N/A"))
            pr_title = pr.get("title", "")
            details = f"{action.capitalize()} pull request [magenta]#{pr_num}[/] [i]'{pr_title}'[/i]"
        elif event_type == "PullRequestReviewCommentEvent":
             action = payload.get("action", "N/A")
             pr_num = payload.get("pull_request", {}).get("number", "N/A")
             details = f"{action.capitalize()} review comment on PR [magenta]#{pr_num}[/]"
        elif event_type == "PullRequestReviewEvent":
             action = payload.get("action", "submitted")
             review = payload.get("review", {})
             state = review.get("state", "")
             pr_num = payload.get("pull_request", {}).get("number", "N/A")
             details = f"{action.capitalize()} review ({state}) on PR [magenta]#{pr_num}[/]"
        elif event_type == "WatchEvent":
            action = payload.get("action", "started")
            details = f"{action.capitalize()} watching (starred)"
        elif event_type == "ForkEvent":
             forkee = payload.get("forkee", {})
             forkee_name = forkee.get("full_name", "N/A")
             details = f"Forked repository to [cyan]{forkee_name}[/]"
        elif event_type == "ReleaseEvent":
            action = payload.get("action", "N/A")
            release = payload.get("release", {})
            tag_name = release.get("tag_name", "N/A")
            name = release.get("name", "")
            details = f"{action.capitalize()} release [blue]{tag_name}[/] {'[i]\'' + name + '\'[/i]' if name else ''}"
        else:
            details = f"[dim]Unhandled event type[/]"

        return f"{timestamp} [bold cyan]{event_type:<15}[/] on [yellow]{repo_name}[/]: {details}"


    def action_refresh(self) -> None:
        """Called when the user presses 'r'."""
        try:
            log = self.query_one(GitHubActivityLog)
            log.write_line("[italic green]Refreshing activity...[/]")
            self.run_fetch_worker()
        except Exception as e:
            print(f"Error during refresh action: {e}")


    def action_quit(self) -> None:
        """Called when the user presses 'q'."""
        self.exit()

if __name__ == "__main__":
    app = GitHubActivityApp()
    app.run()
