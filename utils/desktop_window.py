"""Native-window policy shared by the source and packaged launchers."""


def configure_webview(webview):
    # Downloads are opt-in in both Cocoa and WebView2. Set this before creating
    # the window so Streamlit's PDF/Excel buttons can open a native save dialog.
    webview.settings["ALLOW_DOWNLOADS"] = True
    # App links carry the launch token. Keep new-window/modifier-click requests
    # inside the app instead of putting that token in system-browser history.
    # Public external links use utils.ui.external_link_button server-side.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
