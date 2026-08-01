"""Application-wide light/dark theme tokens and live theme switching."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

THEME_MODES = {"system", "light", "dark"}


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    canvas: str
    shell: str
    surface: str
    surface_subtle: str
    divider: str
    border_strong: str
    focus_ring: str
    bg: str
    sidebar: str
    card: str
    border: str
    text: str
    muted: str
    primary: str
    primary_hover: str
    primary_pressed: str
    danger: str
    danger_bg: str
    danger_border: str
    nav_text: str
    list_hover: str
    list_selected: str
    input_disabled: str
    upload_bg: str
    hover_border: str
    progress_bg: str
    scrollbar: str
    chip_bg: str
    chip_text: str
    tag_bg: str
    tag_text: str
    hidden_bg: str
    fallback_bg: str
    fallback_text: str


@dataclass(frozen=True)
class UiMetrics:
    """Shared geometry for the soft desktop component language."""

    radius_control: int = 8
    radius_item: int = 10
    radius_surface: int = 12
    radius_workspace: int = 14
    radius_floating: int = 16
    control_height: int = 36
    compact_height: int = 32
    workspace_gutter: int = 10


UI_METRICS = UiMetrics()


LIGHT_THEME = ThemeTokens(
    name="light",
    canvas="#F3F6FA",
    shell="#F8FAFD",
    surface="#FFFFFF",
    surface_subtle="#F7F9FC",
    divider="#EEF1F5",
    border_strong="#CCD5E3",
    focus_ring="#7EA6FF",
    bg="#F3F6FA",
    sidebar="#F8FAFD",
    card="#FFFFFF",
    border="#E2E7EF",
    text="#1F2329",
    muted="#646A73",
    primary="#3370FF",
    primary_hover="#2860E1",
    primary_pressed="#1F54C9",
    danger="#F54A45",
    danger_bg="#FEF0F0",
    danger_border="#F8B9B7",
    nav_text="#FFFFFF",
    list_hover="#F2F5FA",
    list_selected="#E8F0FF",
    input_disabled="#F0F2F5",
    upload_bg="#F8FAFD",
    hover_border="#B8C8ED",
    progress_bg="#EEF2F8",
    scrollbar="#D0D7E2",
    chip_bg="#EAF0FF",
    chip_text="#315FB8",
    tag_bg="#EEF1F5",
    tag_text="#566074",
    hidden_bg="#FBFCFE",
    fallback_bg="#FFF3D9",
    fallback_text="#744B00",
)

DARK_THEME = ThemeTokens(
    name="dark",
    canvas="#11151C",
    shell="#171D26",
    surface="#1D2530",
    surface_subtle="#19212B",
    divider="#293341",
    border_strong="#435167",
    focus_ring="#7EA6FF",
    bg="#11151C",
    sidebar="#171D26",
    card="#1D2530",
    border="#303A49",
    text="#E8EDF5",
    muted="#9AA6B7",
    primary="#5B8CFF",
    primary_hover="#78A0FF",
    primary_pressed="#4776DB",
    danger="#FF7875",
    danger_bg="#3B2428",
    danger_border="#7A3F45",
    nav_text="#FFFFFF",
    list_hover="#273142",
    list_selected="#2B3D61",
    input_disabled="#272E39",
    upload_bg="#181E27",
    hover_border="#465367",
    progress_bg="#202733",
    scrollbar="#465164",
    chip_bg="#263858",
    chip_text="#A9C2FF",
    tag_bg="#2A313C",
    tag_text="#BAC4D2",
    hidden_bg="#191F29",
    fallback_bg="#3B321F",
    fallback_text="#FFD88A",
)


def normalize_theme_mode(mode: str) -> str:
    normalized = str(mode or "system").strip().lower()
    if normalized not in THEME_MODES:
        raise ValueError(f"unsupported theme mode: {mode}")
    return normalized


def resolve_theme_mode(
    mode: str,
    system_color_scheme: Qt.ColorScheme | None = None,
) -> str:
    """Resolve system/light/dark into the concrete palette to render."""

    normalized = normalize_theme_mode(mode)
    if normalized != "system":
        return normalized
    return (
        "dark"
        if system_color_scheme == Qt.ColorScheme.Dark
        else "light"
    )


def theme_tokens(theme: str) -> ThemeTokens:
    return DARK_THEME if normalize_theme_mode(theme) == "dark" else LIGHT_THEME


def current_theme_name(app: QApplication | None = None) -> str:
    app = app or QApplication.instance()
    if app is None:
        return "light"
    value = app.property("yancuoResolvedTheme")
    return "dark" if value == "dark" else "light"


def app_stylesheet(theme: str = "light") -> str:
    t = theme_tokens(theme)
    m = UI_METRICS
    return f"""
    QWidget {{
        color: {t.text};
        font-size: 13px;
        font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    }}
    QMainWindow, QDialog, QMenu, QTabWidget::pane {{
        background: {t.bg};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QStatusBar {{
        background: {t.card};
        color: {t.muted};
        border-top: 1px solid {t.border};
        padding: 4px 10px;
    }}
    QToolTip {{
        background: {t.card};
        color: {t.text};
        border: 1px solid {t.border};
        padding: 6px 8px;
    }}
    QMenu {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
        padding: 6px;
    }}
    QMenu::item {{
        min-width: 144px;
        padding: 8px 24px 8px 12px;
        margin: 1px 0;
        border-radius: {m.radius_control}px;
    }}
    QMenu::item:selected {{
        background: {t.list_selected};
        color: {t.primary};
    }}
    QMenu::item:disabled {{
        color: {t.muted};
        background: transparent;
    }}
    QMenu::item[danger="true"] {{
        color: {t.danger};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t.border};
        margin: 6px 8px;
    }}

    QFrame#AppSidebar {{
        background: {t.shell};
        border-right: 1px solid {t.divider};
    }}
    QFrame#SidebarToggleRail {{
        background: {t.bg};
        border-right: 1px solid {t.border};
    }}
    QFrame#AppHeader {{
        background: {t.card};
        border-bottom: 1px solid {t.border};
    }}
    QLabel#AppHeaderTitle {{
        font-size: 16px;
        font-weight: 600;
        color: {t.text};
    }}
    QLabel#BrandTitle {{
        font-size: 18px;
        font-weight: 700;
        color: {t.text};
        padding: 4px 0 0 0;
    }}
    QLabel#BrandSubtitle {{
        font-size: 12px;
        color: {t.muted};
        padding-bottom: 8px;
    }}
    QListWidget#MainNav {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px 8px;
    }}
    QListWidget#MainNav::item {{
        height: 40px;
        padding: 8px 14px;
        margin: 2px 0;
        border-radius: 10px;
        color: {t.text};
    }}
    QListWidget#MainNav::item:hover {{
        background: {t.list_hover};
    }}
    QListWidget#MainNav::item:selected {{
        background: {t.list_selected};
        color: {t.primary};
        font-weight: 600;
    }}

    QFrame#PageRoot, QWidget#PageRoot {{
        background: {t.canvas};
    }}
    QFrame#CardFrame {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: {m.radius_surface}px;
    }}
    QWidget#SettingsPage, QWidget#SettingsContent {{
        background: transparent;
    }}
    QScrollArea#SettingsScroll {{
        background: transparent;
        border: none;
    }}
    QListWidget#SettingsNavigation {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: {m.radius_surface}px;
        padding: 6px;
        outline: none;
    }}
    QListWidget#SettingsNavigation::item {{
        min-height: 36px;
        padding: 7px 10px;
        margin: 1px 0;
        border-radius: {m.radius_control}px;
        color: {t.text};
    }}
    QListWidget#SettingsNavigation::item:hover {{
        background: {t.list_hover};
    }}
    QListWidget#SettingsNavigation::item:selected {{
        background: {t.list_selected};
        color: {t.primary};
        font-weight: 600;
    }}
    QFrame#CardFrame[surfaceRole="settings"],
    QFrame#CardFrame[surfaceRole="data"] {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QFrame#DialogSummarySurface, QFrame#DialogToolbar,
    QFrame#DialogActionBar {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QSplitter#DialogWorkspace {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_workspace}px;
    }}
    QSplitter#DialogWorkspace::handle {{
        background: transparent;
        width: {m.workspace_gutter}px;
    }}
    QFrame#DialogSidePane {{
        background: {t.surface_subtle};
        border: none;
        border-radius: {m.radius_surface}px;
        padding: 12px;
    }}
    QFrame#DialogDetailPane, QFrame#DialogContentSurface {{
        background: {t.surface};
        border: none;
        border-radius: {m.radius_surface}px;
        padding: 12px;
    }}
    QListWidget#DialogItemList {{
        background: transparent;
        border: none;
        outline: none;
        selection-background-color: transparent;
        selection-color: {t.text};
    }}
    QListWidget#DialogItemList::item {{
        padding: 6px 10px;
        margin: 1px 0;
        border-radius: 0;
    }}
    QListWidget#DialogItemList::item:hover,
    QListWidget#DialogItemList::item:selected {{
        background: transparent;
        color: {t.text};
    }}
    QListWidget#MainNav:focus, QListWidget#FilterNav:focus,
    QListWidget#ProblemList:focus, QTreeWidget#KnowledgeTree:focus,
    QListWidget#NoteCollectionList:focus, QListWidget#NoteList:focus,
    QListWidget#ReviewSourceList:focus, QListWidget#ReviewWaitingList:focus,
    QTreeWidget#PlanFolderTree:focus, QListWidget#PlanSourceList:focus,
    QListWidget#PlanQueueList:focus, QListWidget#DialogItemList:focus {{
        border: 1px solid {t.focus_ring};
    }}
    QTextEdit#DialogTextSurface {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_control}px;
    }}
    QScrollArea#ImageViewerCanvas {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QLabel#SourceImage {{
        background: transparent;
    }}
    QLabel#PageTitle {{
        font-size: 20px;
        font-weight: 600;
        color: {t.text};
    }}
    QLabel#PageHint, QLabel#MutedLabel {{
        color: {t.muted};
        font-size: 12px;
    }}
    QLabel#FieldError {{
        color: {t.danger};
        font-size: 12px;
    }}
    QLabel#SectionTitle {{
        font-size: 14px;
        font-weight: 600;
        color: {t.text};
    }}
    QLabel#StatusTag, QLabel#StatusTagActive, QLabel#StatusTagSuccess,
    QLabel#StatusTagWarning, QLabel#StatusTagDanger, QLabel#StatusTagMuted {{
        min-height: 20px;
        max-height: 20px;
        padding: 0 6px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 400;
    }}
    QLabel#StatusTag {{ background: {t.tag_bg}; color: {t.tag_text}; }}
    QLabel#StatusTagActive {{ background: {t.list_selected}; color: {t.primary}; }}
    QLabel#StatusTagSuccess {{ background: #EAF7EE; color: #237A3B; }}
    QLabel#StatusTagWarning {{ background: #FFF6E5; color: #9B6500; }}
    QLabel#StatusTagDanger {{ background: {t.danger_bg}; color: {t.danger}; }}
    QLabel#StatusTagMuted {{ background: {t.input_disabled}; color: {t.muted}; }}
    QLabel#LibraryBreadcrumb {{
        color: {t.primary};
        font-size: 12px;
        font-weight: 600;
        padding: 2px 4px;
    }}
    QLabel#ImagePreview {{
        background: {t.upload_bg};
        border: 1px solid {t.border};
        border-radius: 8px;
    }}
    QLabel#DangerLabel {{
        color: {t.danger};
    }}
    QLabel#WarningLabel {{
        color: {t.fallback_text};
    }}
    QLabel#HeroBanner {{
        background: {t.card};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 16px 20px;
        font-size: 16px;
        font-weight: 600;
    }}
    QFrame#ContextBar {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}
    QFrame#ToolbarDivider {{
        background: {t.divider};
        border: none;
        min-height: 22px;
        max-height: 28px;
    }}
    QFrame#WorkflowStepBar {{
        min-height: 44px;
        max-height: 48px;
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QLabel#WorkflowStep {{
        padding: 5px 10px;
        border-radius: {m.radius_control}px;
        color: {t.muted};
        font-size: 12px;
    }}
    QLabel#WorkflowStep[state="completed"] {{
        color: {t.primary};
    }}
    QLabel#WorkflowStep[state="current"] {{
        color: {t.primary};
        background: {t.surface};
        border: 1px solid {t.divider};
        font-weight: 600;
    }}
    QFrame#WorkflowStepConnector {{
        min-width: 8px;
        max-height: 1px;
        background: {t.divider};
        border: none;
    }}
    QFrame#WorkflowStepConnector[state="completed"] {{
        background: {t.focus_ring};
    }}
    QFrame#IntakePrimarySurface, QFrame#IntakeStatusSurface,
    QFrame#IntakeConfirmationSurface {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_workspace}px;
    }}
    QFrame#IntakeSecondarySurface {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QFrame#IntakeActionBar {{
        min-height: 48px;
        max-height: 48px;
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QTabWidget#AIResultTabs::pane {{
        background: {t.surface};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QFrame#SearchToolbar {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QFrame#LibraryViewSwitch {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QFrame#LibraryWorkspace {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_workspace}px;
    }}
    QSplitter#LibraryWorkspaceSplitter::handle {{
        background: transparent;
        width: {m.workspace_gutter}px;
    }}
    QFrame#LibraryNavigationPanel {{
        background: {t.surface_subtle};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QFrame#LibraryListPanel {{
        background: {t.surface};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QFrame#LibraryPanelHeader {{
        min-height: 48px;
        background: transparent;
        border: none;
    }}
    QLabel#PanelTitle {{
        color: {t.text};
        font-size: 14px;
        font-weight: 600;
    }}
    QLabel#PanelHint {{
        color: {t.muted};
        font-size: 12px;
    }}
    QFrame#LibraryPanelFooter {{
        min-height: 56px;
        max-height: 56px;
        background: transparent;
        border: none;
        border-top: 1px solid {t.divider};
    }}
    QListWidget#ProblemList, QListWidget#FilterNav, QTreeWidget#KnowledgeTree {{
        background: transparent;
        border: none;
        border-radius: 0;
        outline: none;
        padding: 4px;
    }}
    QWidget#InlineQuestionItem {{
        background: transparent;
        border: none;
        border-radius: 0;
    }}
    QLabel#QuestionItemTitle {{ color: {t.text}; font-size: 15px; font-weight: 600; }}
    QPushButton#QuestionChevron {{
        background: transparent;
        border: none;
        min-width: 28px;
        max-width: 28px;
        min-height: 28px;
        max-height: 28px;
        padding: 4px;
    }}
    QPushButton#QuestionChevron:hover {{ background: {t.list_hover}; border-radius: 6px; }}
    QLabel#QuestionMetaTag {{
        background: {t.tag_bg};
        color: {t.tag_text};
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 12px;
    }}
    QFrame#QuestionActionBar {{
        background: transparent;
        border: none;
        border-top: 1px solid {t.divider};
    }}
    QPushButton#SearchModeButton {{
        min-height: 20px;
        max-height: 20px;
        padding: 5px 12px;
    }}
    QPushButton#LibraryViewButton {{
        min-height: 28px;
        border: none;
        border-radius: {m.radius_control}px;
        padding: 4px 14px;
    }}
    QPushButton#LibraryViewButton:checked {{
        color: {t.primary};
        background: {t.surface};
        border: 1px solid {t.divider};
        font-weight: 600;
    }}
    QSplitter#ReviewPlanWorkspace::handle,
    QSplitter#ReviewPlanBrowseWorkspace::handle {{
        background: transparent;
        width: {m.workspace_gutter}px;
    }}
    QSplitter#ReviewPlanWorkspace {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_workspace}px;
    }}
    QFrame#PlanDirectoryPane, QFrame#PlanQueuePane {{
        background: {t.surface_subtle};
        border: none;
        border-radius: {m.radius_surface}px;
        padding: 12px;
    }}
    QFrame#PlanContentPane {{
        background: {t.surface};
        border: none;
        border-radius: {m.radius_surface}px;
        padding: 12px;
    }}
    QTreeWidget#PlanFolderTree, QListWidget#PlanSourceList, QListWidget#PlanQueueList {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px 0;
    }}
    QTreeWidget#PlanFolderTree::item, QListWidget#PlanSourceList::item,
    QListWidget#PlanQueueList::item {{
        min-height: 34px;
        padding: 6px 8px;
        margin: 1px 0;
        border-radius: 6px;
    }}
    QTreeWidget#PlanFolderTree::item:hover, QListWidget#PlanSourceList::item:hover,
    QListWidget#PlanQueueList::item:hover {{
        background: transparent;
    }}
    QTreeWidget#PlanFolderTree::item:selected, QListWidget#PlanSourceList::item:selected,
    QListWidget#PlanQueueList::item:selected {{
        background: transparent;
        color: {t.text};
    }}
    QListWidget#ReviewSourceList, QListWidget#ReviewWaitingList {{
        background: transparent;
        border: none;
        outline: none;
        selection-background-color: transparent;
    }}
    QListWidget#ReviewSourceList::item:hover,
    QListWidget#ReviewWaitingList::item:hover,
    QListWidget#ReviewSourceList::item:selected,
    QListWidget#ReviewWaitingList::item:selected {{
        background: transparent;
        color: {t.text};
    }}
    QFrame#ReviewActionCard, QFrame#ReviewPlanSurface,
    QFrame#ReviewGradeSurface {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QLabel#ReviewOverview, QLabel#ReviewSessionOverview {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
        padding: 14px 18px;
        font-size: 15px;
        font-weight: 600;
    }}

    QSplitter#NoteWorkspace::handle {{
        background: transparent;
        width: {m.workspace_gutter}px;
    }}
    QSplitter#NoteWorkspace {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_workspace}px;
    }}
    QFrame#NoteSpacePane, QFrame#NoteLibraryPane {{
        background: {t.surface_subtle};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QStackedWidget#NoteDetailPane {{
        background: {t.surface};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QFrame#NoteEmptyPane {{
        background: {t.surface};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QFrame#ReadingCanvas {{
        background: {t.surface_subtle};
        border: none;
        border-radius: {m.radius_surface}px;
    }}
    QFrame#ReadingCanvasSheet {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
    }}
    QFrame#ReadingCanvas QScrollBar:horizontal {{
        height: 0;
        margin: 0;
    }}
    QListWidget#NoteCollectionList, QListWidget#NoteList,
    QListWidget#NoteBlockList {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px 0;
        selection-background-color: transparent;
        selection-color: {t.text};
    }}
    QListWidget#NoteCollectionList::item {{
        min-height: 36px;
        padding: 6px 10px;
        margin: 1px 0;
        border-radius: 0;
    }}
    QListWidget#NoteList::item {{
        min-height: 54px;
        padding: 8px 10px;
        margin: 1px 0;
        border-radius: 0;
    }}
    QListWidget#NoteBlockList::item {{
        min-height: 34px;
        padding: 6px 8px;
        margin: 1px 0;
        border-radius: 0;
    }}
    QListWidget#NoteCollectionList::item:hover, QListWidget#NoteList::item:hover,
    QListWidget#NoteBlockList::item:hover {{
        background: transparent;
    }}
    QListWidget#NoteCollectionList::item:selected,
    QListWidget#NoteList::item:selected, QListWidget#NoteBlockList::item:selected {{
        background: transparent;
        color: {t.text};
    }}

    QListWidget#FilterNav, QListWidget#ProblemList, QTreeWidget#KnowledgeTree {{
        background: transparent;
        border: none;
        border-radius: 0;
        outline: none;
        padding: 4px;
        selection-background-color: transparent;
        selection-color: {t.text};
    }}
    QTreeWidget#KnowledgeTree {{
        show-decoration-selected: 0;
    }}
    QListWidget#FilterNav::item, QTreeWidget#KnowledgeTree::item {{
        min-height: 36px;
        padding: 6px 12px;
        margin: 1px 0;
        border-radius: 0;
    }}
    QListWidget#ProblemList::item {{ min-height: 0; padding: 0; margin: 0; border-radius: 0; }}
    QListWidget#FilterNav::item:hover, QListWidget#ProblemList::item:hover,
    QTreeWidget#KnowledgeTree::item:hover {{
        background: transparent;
    }}
    QListWidget#FilterNav::item:selected, QListWidget#ProblemList::item:selected,
    QTreeWidget#KnowledgeTree::item:selected {{
        background: transparent;
        color: {t.text};
        font-weight: 600;
    }}
    QListWidget#UploadFileList, QListWidget#AnswerImageList {{
        background: {t.upload_bg};
        border: 1px solid {t.border};
        border-radius: 8px;
        outline: none;
        padding: 6px;
    }}
    QListWidget#UploadFileList::item, QListWidget#AnswerImageList::item {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 6px;
        margin: 3px;
    }}
    QListWidget#UploadFileList::item:hover, QListWidget#AnswerImageList::item:hover {{
        background: {t.list_hover};
        border-color: {t.hover_border};
    }}
    QListWidget#UploadFileList::item:selected, QListWidget#AnswerImageList::item:selected {{
        background: {t.list_selected};
        border-color: {t.primary};
        color: {t.text};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: {m.radius_control}px;
        padding: 6px 10px;
        min-height: 22px;
        selection-background-color: {t.list_selected};
    }}
    QComboBox {{
        padding-right: 32px;
    }}
    QComboBox:hover {{
        background: {t.surface_subtle};
        border-color: {t.hover_border};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 28px;
        border: none;
        border-top-right-radius: {m.radius_control}px;
        border-bottom-right-radius: {m.radius_control}px;
    }}
    QComboBox::drop-down:hover {{
        background: {t.list_hover};
    }}
    QComboBox::down-arrow {{
        width: 9px;
        height: 9px;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border: 1px solid {t.focus_ring};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
    QSpinBox:disabled, QComboBox:disabled {{
        color: {t.muted};
        background: {t.input_disabled};
    }}
    QComboBox QAbstractItemView {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.divider};
        border-radius: {m.radius_surface}px;
        outline: none;
        padding: 4px;
        selection-background-color: transparent;
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 30px;
        padding: 0 10px;
        margin: 1px 2px;
        border-radius: {m.radius_control}px;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background: {t.list_hover};
    }}
    QComboBox QAbstractItemView::item:selected {{
        background: {t.list_selected};
        color: {t.primary};
    }}
    QTreeWidget, QTableWidget, QTableView {{
        background: {t.card};
        alternate-background-color: {t.sidebar};
        color: {t.text};
        border: 1px solid {t.border};
        gridline-color: transparent;
        selection-background-color: {t.list_selected};
        selection-color: {t.text};
    }}
    QHeaderView::section {{
        background: {t.sidebar};
        color: {t.text};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 8px 12px;
        font-size: 12px;
        font-weight: 500;
    }}
    QLineEdit#SearchEdit {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_item}px;
        padding: 8px 14px;
        min-height: 20px;
    }}
    QComboBox#SearchScopeCombo {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_control}px;
        padding: 5px 30px 5px 8px;
        min-height: 20px;
    }}
    QLineEdit#SearchInput {{
        background: {t.surface};
        border: 1px solid {t.divider};
        border-radius: {m.radius_control}px;
        padding: 5px 8px;
        min-height: 20px;
        max-height: 20px;
    }}
    QLineEdit#SearchInput:focus {{
        border-color: {t.focus_ring};
    }}

    QPushButton {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: {m.radius_control}px;
        padding: 6px 12px;
        min-height: 20px;
    }}
    QPushButton:hover {{
        background: {t.list_hover};
        border-color: {t.hover_border};
    }}
    QPushButton:pressed {{
        background: {t.list_selected};
    }}
    QPushButton:disabled {{
        color: {t.muted};
        background: {t.input_disabled};
    }}
    QPushButton#PrimaryButton {{
        background: {t.primary};
        color: white;
        border: none;
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {t.primary_hover};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {t.primary_pressed};
    }}
    QPushButton#PrimaryButton:disabled {{
        background: {t.input_disabled};
        color: {t.muted};
        border: 1px solid {t.border};
    }}
    QPushButton#DangerButton {{
        background: {t.card};
        color: {t.danger};
        border: 1px solid {t.danger_border};
    }}
    QPushButton#DangerButton:hover {{
        background: {t.danger_bg};
    }}
    QPushButton#GhostButton {{
        background: transparent;
        border: none;
        color: {t.primary};
        padding: 6px 10px;
    }}
    QPushButton#GhostButton:hover {{
        background: {t.list_hover};
        border-radius: 8px;
    }}
    QPushButton#IconButton {{
        padding: 0;
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
    }}
    QPushButton#SearchModeButton, QPushButton#LibraryViewButton {{
        background: transparent;
        color: {t.muted};
        border: 1px solid transparent;
        border-radius: 6px;
    }}
    QPushButton#SearchModeButton {{
        min-width: 78px;
    }}
    QPushButton#LibraryViewButton {{
        min-width: 92px;
    }}
    QPushButton#SearchModeButton:hover:!checked:!disabled,
    QPushButton#LibraryViewButton:hover:!checked:!disabled {{
        background: {t.list_hover};
        border-color: {t.hover_border};
    }}
    QPushButton#SearchModeButton:checked, QPushButton#LibraryViewButton:checked {{
        background: {t.list_selected};
        color: {t.primary};
        border-color: {t.primary};
        font-weight: 600;
    }}
    QPushButton#SearchModeButton:focus, QPushButton#LibraryViewButton:focus {{
        border-color: {t.focus_ring};
    }}
    QPushButton#SearchModeButton:disabled, QPushButton#LibraryViewButton:disabled {{
        background: {t.input_disabled};
        color: {t.muted};
        border-color: {t.border};
    }}
    QPushButton#SegmentButton {{
        background: {t.card};
        color: {t.muted};
        min-width: 72px;
    }}
    QPushButton#SegmentButton:checked {{
        background: {t.list_selected};
        color: {t.primary};
        border-color: {t.primary};
        font-weight: 600;
    }}
    QPushButton#ThemeModeButton {{
        min-width: 76px;
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: {m.radius_control}px;
        padding: 6px 14px;
    }}
    QPushButton#ThemeModeButton:hover:!checked:!disabled {{
        background: {t.list_hover};
        border-color: {t.hover_border};
    }}
    QPushButton#ThemeModeButton:checked {{
        background: {t.primary};
        color: {t.nav_text};
        border-color: {t.primary};
        font-weight: 600;
    }}
    QPushButton#ThemeModeButton:checked:hover {{
        background: {t.primary_hover};
        border-color: {t.primary_hover};
    }}
    QPushButton#ThemeModeButton:focus {{
        border: 2px solid {t.focus_ring};
    }}
    QPushButton#ThemeModeButton:disabled {{
        background: {t.input_disabled};
        color: {t.muted};
        border-color: {t.border};
    }}
    QLabel#ThemeModeStatus {{
        color: {t.primary};
        font-weight: 600;
        padding: 6px 0;
    }}

    QTabWidget::pane {{
        border: none;
        background: {t.card};
    }}
    QTabBar::tab {{
        background: transparent;
        color: {t.muted};
        border: none;
        border-bottom: 2px solid transparent;
        padding: 8px 12px;
    }}
    QTabBar::tab:selected {{
        background: transparent;
        color: {t.primary};
        border-bottom-color: {t.primary};
        font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{
        background: {t.list_hover};
        color: {t.text};
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
    }}

    QProgressBar {{
        background: {t.progress_bg};
        border: 1px solid {t.border};
        border-radius: 7px;
        min-height: 14px;
        text-align: center;
        color: {t.text};
    }}
    QProgressBar::chunk {{
        background: {t.primary};
        border-radius: 6px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.scrollbar};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QSplitter::handle {{
        background: transparent;
        width: 6px;
    }}
    QFrame#BatchActionBar {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 8px;
    }}
    QFrame#ToastMessage {{
        background: transparent;
        border: none;
    }}
    QFrame#ToastContent {{
        background: {t.card};
        border: 1px solid {t.border_strong};
        border-bottom: none;
        border-top-left-radius: {m.radius_floating}px;
        border-top-right-radius: {m.radius_floating}px;
    }}
    QFrame#ToastProgressFrame {{
        background: {t.card};
        border: 1px solid {t.border_strong};
        border-bottom-left-radius: {m.radius_floating}px;
        border-bottom-right-radius: {m.radius_floating}px;
    }}
    QProgressBar#ToastProgress {{
        min-height: 4px;
        max-height: 4px;
        border: none;
        border-radius: 2px;
        background: {t.progress_bg};
    }}
    QProgressBar#ToastProgress::chunk {{
        border-radius: 2px;
        background: #7c5cff;
    }}
    QFrame#CompletionNotification {{
        background: {t.card};
        border: 1px solid {t.border_strong};
        border-radius: {m.radius_floating}px;
    }}
    QLabel#CompletionNotificationTitle {{
        color: {t.text};
        font-weight: 600;
    }}
    QProgressBar#CompletionNotificationProgress {{
        max-height: 4px;
        border: 0;
        border-radius: 2px;
        background: {t.progress_bg};
    }}
    QProgressBar#CompletionNotificationProgress::chunk {{
        border-radius: 2px;
        background: {t.primary};
    }}
    QLabel#ToastText {{
        color: {t.text};
        font-size: 13px;
    }}
    QFrame#LoadingSkeleton {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 8px;
    }}
    QFrame#StateNotice {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_control}px;
    }}
    QFrame#StateNotice[state="loading"] {{
        background: {t.progress_bg};
        border-color: {t.focus_ring};
    }}
    QFrame#StateNotice[state="success"] {{
        background: {t.chip_bg};
        border-color: {t.focus_ring};
    }}
    QFrame#StateNotice[state="error"] {{
        background: {t.danger_bg};
        border-color: {t.danger_border};
    }}
    QFrame#StateNotice[state="error"] QLabel {{
        color: {t.danger};
    }}
    QFrame#StateNotice[state="disabled"] {{
        background: {t.input_disabled};
        border-color: {t.border};
    }}
    QFrame#StateNotice[state="disabled"] QLabel {{
        color: {t.muted};
    }}
    QFrame#StateNotice[state="permission"] {{
        background: {t.fallback_bg};
        border-color: {t.hover_border};
    }}
    QFrame#StateNotice[state="permission"] QLabel {{
        color: {t.fallback_text};
    }}
    QFrame#CompactStateNotice {{
        background: {t.surface_subtle};
        border: 1px solid {t.divider};
        border-radius: {m.radius_control}px;
    }}
    QFrame#CompactStateNotice[state="loading"] {{
        background: {t.progress_bg};
        border-color: {t.focus_ring};
    }}
    QFrame#CompactStateNotice[state="success"] {{
        background: {t.chip_bg};
        border-color: {t.focus_ring};
    }}
    QFrame#CompactStateNotice[state="error"] {{
        background: {t.danger_bg};
        border-color: {t.danger_border};
    }}
    QFrame#CompactStateNotice[state="error"] QLabel {{
        color: {t.danger};
    }}
    QFrame#CompactStateNotice[state="disabled"] {{
        background: {t.input_disabled};
        border-color: {t.border};
    }}
    QFrame#CompactStateNotice[state="disabled"] QLabel {{
        color: {t.muted};
    }}
    QFrame#SkeletonLineLong, QFrame#SkeletonLineShort {{
        background: {t.input_disabled};
        border: none;
        border-radius: 4px;
    }}
    QFrame#SkeletonLineShort {{
        max-width: 40%;
    }}
    QDialog {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: {m.radius_floating}px;
    }}
    """


def _app_palette(tokens: ThemeTokens) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.bg,
        QPalette.ColorRole.WindowText: tokens.text,
        QPalette.ColorRole.Base: tokens.card,
        QPalette.ColorRole.AlternateBase: tokens.sidebar,
        QPalette.ColorRole.ToolTipBase: tokens.card,
        QPalette.ColorRole.ToolTipText: tokens.text,
        QPalette.ColorRole.Text: tokens.text,
        QPalette.ColorRole.Button: tokens.card,
        QPalette.ColorRole.ButtonText: tokens.text,
        QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Link: tokens.primary,
        QPalette.ColorRole.Highlight: tokens.primary,
        QPalette.ColorRole.HighlightedText: tokens.nav_text,
        QPalette.ColorRole.PlaceholderText: tokens.muted,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(tokens.muted),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(tokens.muted),
    )
    return palette


class ThemeManager(QObject):
    """Apply one resolved palette to Qt widgets and embedded HTML readers."""

    theme_changed = Signal(str)

    def __init__(self, app: QApplication, mode: str = "system") -> None:
        super().__init__(app)
        self.app = app
        self.mode = normalize_theme_mode(mode)
        self.resolved = ""
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_theme_changed)
        self.apply()

    def set_mode(self, mode: str) -> str:
        self.mode = normalize_theme_mode(mode)
        self.app.setProperty("yancuoThemeMode", self.mode)
        return self.apply()

    def apply(self) -> str:
        hints = self.app.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else None
        resolved = resolve_theme_mode(self.mode, scheme)
        tokens = theme_tokens(resolved)
        self.app.setPalette(_app_palette(tokens))
        self.app.setStyleSheet(app_stylesheet(resolved))
        self.app.setProperty("yancuoThemeMode", self.mode)
        self.app.setProperty("yancuoResolvedTheme", resolved)
        changed = resolved != self.resolved
        self.resolved = resolved
        if changed:
            self.theme_changed.emit(resolved)
        return resolved

    def _on_system_theme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self.mode == "system":
            self.apply()


def get_theme_manager(app: QApplication | None = None) -> ThemeManager | None:
    app = app or QApplication.instance()
    return getattr(app, "_yancuo_theme_manager", None) if app else None


def apply_app_theme(
    app: QApplication,
    mode: str = "system",
) -> ThemeManager:
    font = QFont("Microsoft YaHei UI", 10)
    if not font.exactMatch():
        font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyle("Fusion")
    manager = get_theme_manager(app)
    if manager is None:
        manager = ThemeManager(app, mode)
        app._yancuo_theme_manager = manager
    else:
        manager.set_mode(mode)
    return manager
