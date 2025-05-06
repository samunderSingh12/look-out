# github_activity.py (Enhanced error reporting)

import os
import requests
from datetime import datetime
from dotenv import load_dotenv
import traceback
from typing import Any, Coroutine, Optional, Dict, List

from rich.text import Text

from textual.app import App, ComposeResult, Binding
from textual.containers import Container # Not explicitly used, could be removed
from textual.reactive import reactive
from textual.widgets import (
    Header,
    Footer,
    Log,
    LoadingIndicator,
    TabbedContent,
    TabPane,
    ListView,
    ListItem,
    Label,
    Static,
)
from textual.message import Message

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
    print("\nEnsure your token has 'notifications' and 'repo' scopes for full functionality.")
    exit(1)

API_BASE = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "Authorization": f"token {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}
DEFAULT_TIMEOUT = 15

# --- Custom Messages for Worker Results ---
# (Messages remain the same)
class ActivityFetched(Message):
    def __init__(self, events: Optional[List[Dict]] = None, error: Optional[Exception] = None) -> None:
        self.events = events
        self.error = error
        super().__init__()

class NotificationsFetched(Message):
    def __init__(self, notifications: Optional[List[Dict]] = None, error: Optional[Exception] = None) -> None:
        self.notifications = notifications
        self.error = error
        super().__init__()

class ReposFetched(Message):
    def __init__(self, repos: Optional[List[Dict]] = None, error: Optional[Exception] = None, repo_type: str = "my") -> None:
        self.repos = repos
        self.error = error
        self.repo_type = repo_type # 'my' or 'starred'
        super().__init__()

# --- Custom ListItems ---
# (ListItems remain the same)
class NotificationItem(ListItem):
    def __init__(self, notification_data: Dict) -> None:
        super().__init__(classes="notification_item")
        self.notification_data = notification_data
        self.reason = notification_data.get("reason", "unknown")
        self.title = notification_data.get("subject", {}).get("title", "No Title")
        self.repo = notification_data.get("repository", {}).get("full_name", "No Repo")
        self.type = notification_data.get("subject", {}).get("type", "Unknown")
        self.updated_at = self._format_time(notification_data.get("updated_at"))

    def _format_time(self, time_str: Optional[str]) -> str:
        if not time_str: return ""
        try:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            local_dt = dt.astimezone()
            return local_dt.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            return time_str

    def compose(self) -> ComposeResult:
        yield Label(f"[[cyan]{self.type}[/]] [bold]{self.title}[/] ([dim]{self.reason}[/])")
        yield Label(f"  Repo: [yellow]{self.repo}[/] - {self.updated_at}")

class RepoItem(ListItem):
    def __init__(self, repo_data: Dict) -> None:
        super().__init__(classes="repo_item")
        self.repo_data = repo_data
        self.name = repo_data.get("full_name", "No Name")
        self.description = repo_data.get("description") or "[dim]No description[/]"
        self.stars = repo_data.get("stargazers_count", 0)
        self.language = repo_data.get("language") or "N/A"
        self.updated_at = self._format_time(repo_data.get("pushed_at"))

    def _format_time(self, time_str: Optional[str]) -> str:
        if not time_str: return ""
        try:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            local_dt = dt.astimezone()
            return local_dt.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            return time_str

    def compose(self) -> ComposeResult:
        yield Label(f"[bold yellow]{self.name}[/] ([green]{self.language}[/], ★{self.stars})")
        yield Label(f"  {self.description}")
        yield Label(f"  [dim]Last push:[/dim] {self.updated_at}")

# --- Main App ---
class GitHubTUIApp(App):

    CSS_PATH = "github_activity.css"
    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh_active_tab", "Refresh", show=True),
        Binding("tab", "focus_next", "Focus Next", show=False),
        Binding("shift+tab", "focus_previous", "Focus Prev", show=False),
    ]

    loading = reactive(False)
    status_message = reactive("") # Reactive variable for status text
    loaded_tabs = reactive(set())

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="main-loading")
        with TabbedContent(initial="activity"):
            with TabPane("Activity", id="activity"):
                yield Log(highlight=True, id="activity-log") # No markup=True
            with TabPane("Notifications", id="notifications"):
                yield ListView(id="notifications-list")
            with TabPane("My Repos", id="my-repos"):
                 yield ListView(id="my-repos-list")
            with TabPane("Starred", id="starred"):
                 yield ListView(id="starred-list")
        # Ensure the Static widget for status bar has an ID
        yield Static(id="status-bar", expand=True)
        yield Footer()

    # --- Status and Loading Updates ---

    def watch_loading(self, loading: bool) -> None:
        """Show/hide loading indicator."""
        try:
            indicator = self.query_one("#main-loading", LoadingIndicator)
            indicator.styles.display = "block" if loading else "none"
            # --- Clear status only if finishing loading AND status was a loading message ---
            if not loading and self.status_message.startswith("[italic]Loading"):
                 self.update_status("") # Clear loading message
        except Exception as e:
            print(f"Error in watch_loading: {e}")


    def update_status(self, message: str, error: bool = False) -> None:
        """Update the status bar, ensuring it exists."""
        # Update reactive var first
        prefix = "[bold red]Error: [/]" if error else ""
        self.status_message = f"{prefix}{message}"
        try:
            # Query the status bar reliably
            status_bar = self.query_one("#status-bar", Static)
            status_bar.update(self.status_message)
        except Exception as e:
             # Fallback if status bar isn't ready or query fails
             print(f"STATUS UPDATE: {self.status_message}")
             print(f"(Error updating status bar widget: {e})")


    # --- Data Fetching Workers ---

    def _fetch_data(self, url: str, params: Optional[Dict] = None) -> List[Dict]:
        """Generic function to fetch data from GitHub API."""
        # (Error handling remains the same - raises exceptions on failure)
        try:
            response = requests.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT, params=params)
            #print(f"DEBUG: Request URL: {response.url}") # Uncomment for URL debugging
            #print(f"DEBUG: Response Status: {response.status_code}") # Uncomment for status debugging
            response.raise_for_status() # Raises HTTPError for 4xx/5xx
            return response.json()
        except requests.exceptions.Timeout as e:
            raise ConnectionError(f"Request timed out after {DEFAULT_TIMEOUT}s: {url}") from e
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            msg = f"API Error ({status}) for {url}"
            try:
                details = e.response.json().get('message', '')
                if details: msg += f" - {details}"
                # Add specific hints for common errors
                if status == 401: msg += " (Check PAT validity/permissions?)"
                if status == 403: msg += " (Rate limit or insufficient PAT scope? Required: repo, notifications)"
                if status == 404: msg += " (User/Resource not found?)"
            except ValueError:
                 msg += f" - Non-JSON response: {e.response.text[:100]}" # Show snippet if not JSON
            raise PermissionError(msg) from e # Use PermissionError for auth/scope issues
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Network Error connecting to {url}: {e}") from e
        except Exception as e:
            # Catch broader errors during request/parsing
            raise RuntimeError(f"Unexpected error during fetch {url}: {e}") from e

    def _run_worker(self, coro: Coroutine[Any, Any, None], force: bool = False) -> None:
        """Helper to run workers and manage loading state."""
        if self.loading and not force:
             self.update_status("[yellow]Operation already in progress.[/]")
             return
        self.loading = True # Set loading before starting worker
        # Clear previous error status when starting a new load
        if self.status_message.startswith("[bold red]Error:"):
            self.update_status("")
        print(f"DEBUG: Starting worker for: {coro.__name__}") # Debug print
        self.run_worker(coro, exclusive=True, group=f"fetch-{coro.__name__}")

    async def fetch_activity(self) -> None:
        """Worker coroutine to fetch activity."""
        self.update_status("[italic]Loading activity...[/]")
        error = None
        events = None
        try:
            events = await self.loop.run_in_executor(
                None, self._fetch_data, f"{API_BASE}/users/{GITHUB_USER}/events/public", {"per_page": 50}
            )
        except Exception as e:
            print(f"ERROR in fetch_activity: {e}\n{traceback.format_exc()}") # Print detailed traceback
            error = e # Store error to pass in message
        finally:
            # Always post message, even on error
            self.post_message(ActivityFetched(events=events, error=error))


    async def fetch_notifications(self) -> None:
        """Worker coroutine to fetch notifications."""
        self.update_status("[italic]Loading notifications...[/]")
        error = None
        notifications = None
        try:
            notifications = await self.loop.run_in_executor(
                None, self._fetch_data, f"{API_BASE}/notifications", {"per_page": 50}
            )
        except Exception as e:
            print(f"ERROR in fetch_notifications: {e}\n{traceback.format_exc()}")
            error = e
        finally:
            self.post_message(NotificationsFetched(notifications=notifications, error=error))


    async def fetch_repos(self, repo_type: str) -> None:
        """Worker coroutine to fetch repositories ('my' or 'starred')."""
        self.update_status(f"[italic]Loading {repo_type} repositories...[/]")
        error = None
        repos = None
        try:
            if repo_type == "my":
                url = f"{API_BASE}/user/repos"
                # Ensure affiliation covers common cases
                params = {"sort": "pushed", "per_page": 50, "affiliation": "owner,collaborator"}
            elif repo_type == "starred":
                url = f"{API_BASE}/user/starred"
                params = {"sort": "created", "per_page": 50} # Correct sort for starred
            else:
                raise ValueError("Invalid repo_type requested")

            repos = await self.loop.run_in_executor(None, self._fetch_data, url, params)
        except Exception as e:
            print(f"ERROR in fetch_repos ({repo_type}): {e}\n{traceback.format_exc()}")
            error = e
        finally:
            self.post_message(ReposFetched(repos=repos, error=error, repo_type=repo_type))

    # --- Message Handlers (with improved error handling) ---

    def _handle_fetch_error(self, error: Exception, context: str):
        """Centralized error handling for fetch results."""
        print(f"HANDLING ERROR for {context}: {error}") # Debug print
        self.update_status(f"{error}", error=True)
        # Optionally log to the specific widget as well
        try:
            widget = None
            if context == "activity": widget = self.query_one("#activity-log", Log)
            elif context == "notifications": widget = self.query_one("#notifications-list", ListView)
            elif context == "my-repos": widget = self.query_one("#my-repos-list", ListView)
            elif context == "starred": widget = self.query_one("#starred-list", ListView)

            if widget:
                if isinstance(widget, ListView):
                    widget.clear()
                    widget.append(ListItem(Label(f"[bold red]Error loading {context}:[/]\n{error}")))
                elif isinstance(widget, Log):
                    widget.clear()
                    widget.write_line(f"[bold red]Error loading {context}:[/]\n{error}")
        except Exception as e_widget:
             print(f"Error updating widget after fetch error: {e_widget}") # Log secondary error
        finally:
             self.loading = False # Ensure loading is always reset on error


    def on_activity_fetched(self, message: ActivityFetched) -> None:
        """Handle results from fetch_activity worker."""
        print("DEBUG: on_activity_fetched received")
        if message.error:
            self._handle_fetch_error(message.error, "activity")
            return # Stop processing if there was an error

        try:
            log = self.query_one("#activity-log", Log)
            log.clear()
            if message.events is not None:
                if not message.events:
                    log.write_line("[yellow]No recent public activity found.[/]")
                else:
                    for event in message.events:
                        log.write_line(self.format_event(event))
                self.update_status("Activity loaded.")
                self.loaded_tabs.add("activity")
            else:
                 # This case shouldn't happen if error handling in worker is correct
                 self.update_status("Activity fetch returned invalid data (None).", error=True)
                 log.write_line("[red]Failed to process activity data.[/]")
        except Exception as e:
            # Catch errors during UI update
            self.update_status(f"Error updating activity UI: {e}", error=True)
            print(f"Error in on_activity_fetched UI update: {e}\n{traceback.format_exc()}")
        finally:
            self.loading = False


    def on_notifications_fetched(self, message: NotificationsFetched) -> None:
        """Handle results from fetch_notifications worker."""
        print("DEBUG: on_notifications_fetched received")
        if message.error:
            self._handle_fetch_error(message.error, "notifications")
            return

        try:
            list_view = self.query_one("#notifications-list", ListView)
            list_view.clear()
            if message.notifications is not None:
                if not message.notifications:
                     list_view.append(ListItem(Label("[yellow]No unread notifications.[/]")))
                else:
                     for notification in message.notifications:
                         list_view.append(NotificationItem(notification))
                count = len(message.notifications)
                self.update_status(f"{count} notifications loaded.")
                self.loaded_tabs.add("notifications")
            else:
                 self.update_status("Notifications fetch returned invalid data (None).", error=True)
                 list_view.append(ListItem(Label("[red]Failed to process notification data.[/]")))
        except Exception as e:
            self.update_status(f"Error updating notifications UI: {e}", error=True)
            print(f"Error in on_notifications_fetched UI update: {e}\n{traceback.format_exc()}")
        finally:
            self.loading = False

    def on_repos_fetched(self, message: ReposFetched) -> None:
        """Handle results from fetch_repos worker."""
        repo_context = "my-repos" if message.repo_type == "my" else "starred"
        print(f"DEBUG: on_repos_fetched received for {repo_context}")
        if message.error:
            self._handle_fetch_error(message.error, repo_context)
            return

        list_id = f"#{message.repo_type}-repos-list" # e.g. #my-repos-list
        try:
            list_view = self.query_one(list_id, ListView)
            list_view.clear()
            if message.repos is not None:
                if not message.repos:
                     list_view.append(ListItem(Label(f"[yellow]No {message.repo_type} repositories found.[/]")))
                else:
                     for repo in message.repos:
                         list_view.append(RepoItem(repo))
                count = len(message.repos)
                self.update_status(f"{count} {message.repo_type} repositories loaded.")
                self.loaded_tabs.add(repo_context)
            else:
                self.update_status(f"{message.repo_type.capitalize()} repo fetch failed (invalid data).", error=True)
                list_view.append(ListItem(Label(f"[red]Failed to process {message.repo_type} repository data.[/]")))
        except Exception as e:
             self.update_status(f"Error updating {repo_context} UI: {e}", error=True)
             print(f"Error in on_repos_fetched UI update ({repo_context}): {e}\n{traceback.format_exc()}")
        finally:
            self.loading = False


    # --- Event Handlers ---

    def on_mount(self) -> None:
        """Called when the app is first mounted. Load initial tab."""
        self.update_status("App mounted. Fetching initial data...")
        # Use call_later to allow the UI to settle before the first fetch
        self.call_later(self._load_initial_tab_data)

    def _load_initial_tab_data(self) -> None:
         """Load data for the initial active tab."""
         try:
             tabs = self.query_one(TabbedContent)
             active_tab_id = tabs.active
             print(f"DEBUG: Initial tab is '{active_tab_id}'")
             if active_tab_id:
                 self._load_data_for_tab(active_tab_id, force_load=True)
         except Exception as e:
              self.update_status(f"Error on mount loading initial tab: {e}", error=True)
              print(f"Error in _load_initial_tab_data: {e}\n{traceback.format_exc()}")


    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Called when a tab is switched. Load data if not already loaded."""
        try:
            # Use event.pane.id which should be reliable
            tab_id = str(event.pane.id) if event.pane.id else None # Ensure it's a string
            print(f"DEBUG: Tab activated: '{tab_id}'")
            if tab_id and tab_id not in self.loaded_tabs:
                self.update_status(f"Loading {tab_id}...")
                self._load_data_for_tab(tab_id)
            elif tab_id:
                 # Update status even if already loaded
                 self.update_status(f"Active tab: {tab_id}")
            else:
                 print("WARN: Tab activated with no ID?")
        except Exception as e:
            self.update_status(f"Error switching tab: {e}", error=True)
            print(f"Error in on_tabbed_content_tab_activated: {e}\n{traceback.format_exc()}")


    def _load_data_for_tab(self, tab_id: str, force_load: bool = False):
        """Determine which data fetching worker to run based on tab ID."""
        print(f"DEBUG: _load_data_for_tab called for '{tab_id}', force={force_load}")
        if self.loading and not force_load:
            print(f"DEBUG: Skipping load for '{tab_id}', already loading.")
            return

        coro = None
        if tab_id == "activity":
            coro = self.fetch_activity()
        elif tab_id == "notifications":
            coro = self.fetch_notifications()
        elif tab_id == "my-repos":
            coro = self.fetch_repos(repo_type="my")
        elif tab_id == "starred":
            coro = self.fetch_repos(repo_type="starred")

        if coro:
            self._run_worker(coro, force=force_load)
        else:
             print(f"WARN: No fetch action defined for tab_id '{tab_id}'")


    # --- Actions ---

    def action_refresh_active_tab(self) -> None:
        """Refresh data for the currently active tab."""
        print("DEBUG: Refresh action triggered")
        try:
            tabs = self.query_one(TabbedContent)
            active_tab_id = tabs.active
            if active_tab_id:
                self.update_status(f"Refreshing {active_tab_id}...")
                # Clear loaded status so it fetches again
                if active_tab_id in self.loaded_tabs:
                    print(f"DEBUG: Removing '{active_tab_id}' from loaded_tabs for refresh.")
                    self.loaded_tabs.remove(active_tab_id)

                # Clear visual list immediately if applicable
                widget_to_clear = None
                try:
                    if active_tab_id == "activity": widget_to_clear = self.query_one("#activity-log", Log)
                    elif active_tab_id == "notifications": widget_to_clear = self.query_one("#notifications-list", ListView)
                    elif active_tab_id == "my-repos": widget_to_clear = self.query_one("#my-repos-list", ListView)
                    elif active_tab_id == "starred": widget_to_clear = self.query_one("#starred-list", ListView)

                    if widget_to_clear:
                         # Check widget has a 'clear' method before calling
                         if hasattr(widget_to_clear, 'clear'):
                             widget_to_clear.clear()
                             print(f"DEBUG: Cleared widget for '{active_tab_id}'.")
                         else:
                             print(f"WARN: Widget for '{active_tab_id}' has no clear method.")

                except Exception as e_clear:
                    print(f"WARN: Could not clear widget for '{active_tab_id}' on refresh: {e_clear}")

                # Force the load
                self._load_data_for_tab(active_tab_id, force_load=True)
            else:
                self.update_status("No active tab to refresh.")
                print("WARN: Refresh action triggered but no active tab found.")
        except Exception as e:
            self.update_status(f"Error during refresh: {e}", error=True)
            print(f"Error in action_refresh_active_tab: {e}\n{traceback.format_exc()}")

    # --- Formatting (Activity - unchanged) ---
    def format_event(self, event: dict) -> str:
        # (This long formatting function remains exactly the same as before)
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
        if event_type == "PushEvent":
            branch = payload.get('ref', 'N/A').split('/')[-1]; size = payload.get('size', 0); plural = "s" if size != 1 else ""; details = f"Pushed {size} commit{plural} to [blue]{branch}[/]"
        elif event_type == "CreateEvent":
            ref_type = payload.get("ref_type", "N/A"); ref = payload.get("ref")
            if ref_type == "repository": details = f"Created repository"
            elif ref_type == "branch" and ref: details = f"Created branch [blue]{ref}[/]"
            elif ref_type == "tag" and ref: details = f"Created tag [blue]{ref}[/]"
            else: details = f"Created {ref_type}"
        elif event_type == "DeleteEvent":
             ref_type = payload.get("ref_type", "N/A"); ref = payload.get("ref", "N/A"); details = f"Deleted {ref_type} [blue]{ref}[/]"
        elif event_type == "IssuesEvent":
            action = payload.get("action", "N/A"); issue = payload.get("issue", {}); issue_num = issue.get("number", "N/A"); issue_title = issue.get("title", ""); details = f"{action.capitalize()} issue [green]#{issue_num}[/] [i]'{issue_title}'[/i]"
        elif event_type == "IssueCommentEvent":
            action = payload.get("action", "N/A"); issue_num = payload.get("issue", {}).get("number", "N/A"); details = f"{action.capitalize()} comment on issue [green]#{issue_num}[/]"
        elif event_type == "PullRequestEvent":
            action = payload.get("action", "N/A"); pr = payload.get("pull_request", {}); pr_num = payload.get("number", pr.get("number", "N/A")); pr_title = pr.get("title", ""); details = f"{action.capitalize()} pull request [magenta]#{pr_num}[/] [i]'{pr_title}'[/i]"
        elif event_type == "PullRequestReviewCommentEvent":
             action = payload.get("action", "N/A"); pr_num = payload.get("pull_request", {}).get("number", "N/A"); details = f"{action.capitalize()} review comment on PR [magenta]#{pr_num}[/]"
        elif event_type == "PullRequestReviewEvent":
             action = payload.get("action", "submitted"); review = payload.get("review", {}); state = review.get("state", ""); pr_num = payload.get("pull_request", {}).get("number", "N/A"); details = f"{action.capitalize()} review ({state}) on PR [magenta]#{pr_num}[/]"
        elif event_type == "WatchEvent":
            action = payload.get("action", "started"); details = f"{action.capitalize()} watching (starred)"
        elif event_type == "ForkEvent":
             forkee = payload.get("forkee", {}); forkee_name = forkee.get("full_name", "N/A"); details = f"Forked repository to [cyan]{forkee_name}[/]"
        elif event_type == "ReleaseEvent":
            action = payload.get("action", "N/A"); release = payload.get("release", {}); tag_name = release.get("tag_name", "N/A"); name = release.get("name", ""); details = f"{action.capitalize()} release [blue]{tag_name}[/] {'[i]\'' + name + '\'[/i]' if name else ''}"
        else: details = f"[dim]Unhandled event type[/]"
        return f"{timestamp} [bold cyan]{event_type:<15}[/] on [yellow]{repo_name}[/]: {details}"


if __name__ == "__main__":
    # Consider adding Textual dev tools for easier debugging:
    # Run with: textual run --dev github_activity.py
    # This provides live inspection and logging.
    app = GitHubTUIApp()
    app.run()
