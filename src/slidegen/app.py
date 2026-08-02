"""Streamlit front end.

Brief → three candidates → pick one → edit the text → download.

Deliberately one plain file. The interesting parts of this project are the
template catalog and the spec contract; the UI is a thin shell over them.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st

# `streamlit run src/slidegen/app.py` executes this file as a script, with no
# package context, so absolute imports need src/ on the path.
if __package__ in (None, ""):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slidegen.config import Settings, get_settings
from slidegen.guards import check_specs
from slidegen.llm.base import ProviderError
from slidegen.llm.factory import get_provider, reset_cache
from slidegen.logging_config import configure_logging
from slidegen.render import render
from slidegen.spec import SlideSpec, validate_spec
from slidegen.template import BUILT_IN, LayoutCatalog, load_catalog
from slidegen.update import UpdateError, apply_update, cached_check
from slidegen.update import reset_cache as reset_update_cache

configure_logging()
logger = logging.getLogger(__name__)

BULLET_MARKER = "- "


# --- state -----------------------------------------------------------------


def _settings() -> Settings:
    """Session settings, seeded from the environment and editable in the sidebar."""
    if "settings" not in st.session_state:
        st.session_state.settings = get_settings().model_copy()
    return st.session_state.settings


@st.cache_resource(show_spinner=False)
def _load(template_path: str) -> tuple[object, LayoutCatalog]:
    """Template and catalog, cached across reruns and keyed on the path."""
    return load_catalog(template_path or None)


def _catalog() -> tuple[object, LayoutCatalog]:
    return _load(_settings().slidegen_template or "")


def _reset_candidates() -> None:
    st.session_state.pop("candidates", None)
    st.session_state.pop("chosen", None)
    st.session_state.pop("edits", None)


# --- editing ---------------------------------------------------------------


def _to_editable(value: str | list[str]) -> str:
    """Bullets become one `- ` line each; a paragraph stays a paragraph."""
    if isinstance(value, str):
        return value
    return "\n".join(f"{BULLET_MARKER}{item}" for item in value)


def _from_editable(text: str, was_list: bool) -> str | list[str]:
    lines = [line.strip() for line in text.splitlines()]
    bulleted = [line for line in lines if line.startswith(BULLET_MARKER)]
    if bulleted and len(bulleted) == len([line for line in lines if line]):
        return [line[len(BULLET_MARKER) :].strip() for line in bulleted]
    if was_list and len([line for line in lines if line]) > 1:
        return [line for line in lines if line]
    return text.strip()


def _edited_spec(spec: SlideSpec, catalog: LayoutCatalog) -> SlideSpec:
    """Render the editor for the chosen candidate and return the edited spec."""
    layout = catalog.layout(spec.layout_index)
    assert layout is not None  # validated before it reached the picker

    placeholders: dict[int, str | list[str]] = {}
    for placeholder in layout.content_placeholders:
        original = spec.placeholders.get(placeholder.idx, "")
        was_list = isinstance(original, list)
        label = f"{placeholder.label}{'  (title)' if placeholder.is_title else ''}"
        text = st.text_area(
            label,
            value=_to_editable(original),
            key=f"edit_{spec.layout_index}_{placeholder.idx}",
            height=90 if placeholder.is_title else 150,
            help="One `- ` per line makes bullets. Plain text makes a paragraph.",
        )
        value = _from_editable(text, was_list)
        if value:
            placeholders[placeholder.idx] = value

    notes = st.text_area("Speaker notes", value=spec.notes or "", key="edit_notes", height=90)

    return SlideSpec(
        layout_index=spec.layout_index,
        rationale=spec.rationale,
        placeholders=placeholders,
        notes=notes.strip() or None,
    )


# --- sidebar ---------------------------------------------------------------


def _sidebar(settings: Settings) -> None:
    with st.sidebar:
        st.subheader("Settings")

        available = settings.enabled_providers()
        provider = st.selectbox(
            "Provider",
            available,
            index=available.index(settings.llm_provider)
            if settings.llm_provider in available
            else 0,
            help="Only providers with complete configuration are listed.",
        )
        model = st.text_input("Model", value=settings.llm_model or "", placeholder=settings.model)
        template_path = st.text_input(
            "Template path",
            value=settings.slidegen_template or "",
            placeholder=BUILT_IN,
            help="A .potx or .pptx. Leave empty to use the python-pptx built-in.",
        )

        if provider == "ford":
            tier = st.radio(
                "Data tier",
                ("secret", "standard"),
                index=0 if settings.ford_tier == "secret" else 1,
                help="Secret is the default: briefs contain unannounced product information.",
            )
        else:
            tier = settings.ford_tier

        changed = (
            provider != settings.llm_provider
            or (model or None) != settings.llm_model
            or (template_path or None) != settings.slidegen_template
            or tier != settings.ford_tier
        )
        if changed:
            st.session_state.settings = settings.model_copy(
                update={
                    "llm_provider": provider,
                    "llm_model": model or None,
                    "slidegen_template": template_path or None,
                    "ford_tier": tier,
                }
            )
            reset_cache()
            _reset_candidates()
            st.rerun()

        st.divider()
        if st.button("Test connection", use_container_width=True):
            _test_connection(settings)

        st.caption(f"Candidates per run: {settings.candidate_count}")

        _update_notice(settings)


def _test_connection(settings: Settings) -> None:
    """Reachability only — deliberately not a trial generation.

    On LM Studio a generation can take a minute and fail for reasons that have
    nothing to do with the connection. Listing models separates the two, and
    names the model that is actually loaded.
    """
    try:
        models = get_provider(settings).ping()
    except ProviderError as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — a traceback in the UI helps nobody
        logger.exception("connection test failed")
        st.error(f"{settings.llm_provider} did not respond: {exc}")
        return

    st.success(f"{settings.llm_provider} responded.")
    if models:
        st.caption("Models available:")
        st.code("\n".join(models[:20]), language=None)
        if settings.llm_model and settings.llm_model not in models:
            st.warning(
                f"`{settings.llm_model}` is not in that list. The server may "
                "reject it, or load a different model than you expect."
            )


def _update_notice(settings: Settings) -> None:
    """Tell the user about a newer release. Never blocks, never self-applies."""
    if not settings.slidegen_update_check:
        return

    status = cached_check(
        settings.slidegen_repo,
        ttl=settings.slidegen_update_ttl,
        token=settings.github_token,
    )
    st.divider()

    if not status.checked:
        st.caption(f"SlideGen {status.current} · {status.error}")
        return
    if not status.available:
        st.caption(f"SlideGen {status.current} · up to date")
        return

    st.info(f"**{status.latest}** is available (you have {status.current}).")
    if status.url:
        st.caption(f"[Release notes]({status.url})")
    if st.button("Update and restart", use_container_width=True):
        try:
            with st.spinner("Updating…"):
                message = apply_update(status.latest or "")
        except UpdateError as exc:
            st.error(str(exc))
        else:
            reset_update_cache()
            st.success(message)


# --- header ----------------------------------------------------------------


def _header(settings: Settings, catalog: LayoutCatalog) -> None:
    st.title("SlideGen")

    columns = st.columns(4)
    columns[0].metric("Provider", settings.llm_provider)
    columns[1].metric("Model", settings.model)
    columns[2].metric("Layouts", len(catalog.usable_layouts))
    if settings.llm_provider == "ford":
        columns[3].metric("Data tier", settings.ford_tier.upper())
        if settings.ford_tier == "secret":
            st.info("**Secret-data gateway.** Cleared for unannounced product information.")
        else:
            st.warning(
                "**Standard gateway.** Do not paste unannounced product information, "
                "timing or pricing into the brief. Switch to the secret tier first."
            )
    else:
        columns[3].metric("Template", "built-in" if catalog.source == BUILT_IN else "custom")


# --- main ------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="SlideGen", page_icon="📊", layout="wide")
    settings = _settings()

    try:
        presentation, catalog = _catalog()
    except FileNotFoundError as exc:
        st.title("SlideGen")
        st.error(f"{exc}\n\nFix the template path in the sidebar.")
        _sidebar(settings)
        return

    _header(settings, catalog)
    _sidebar(settings)

    brief = st.text_area(
        "Brief",
        height=200,
        key="brief",
        placeholder="Paste the brief. What is the point of the slide, and what supports it?",
    )

    # Not disabled on an empty brief: the click that blurs the textarea would be
    # swallowed, and the user would have to press Generate twice.
    if st.button("Generate", type="primary"):
        _reset_candidates()
        if not brief.strip():
            st.warning("Paste a brief first.")
        else:
            with st.spinner(f"Asking {settings.llm_provider}…"):
                try:
                    st.session_state.candidates = get_provider(settings).generate(
                        brief, catalog, settings.candidate_count
                    )
                except ProviderError as exc:
                    st.error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("generation failed")
                    st.error(f"Generation failed: {exc}")

    candidates: list[SlideSpec] = st.session_state.get("candidates", [])
    if not candidates:
        return

    st.subheader("Candidates")
    for column, (position, spec) in zip(
        st.columns(len(candidates)), enumerate(candidates), strict=True
    ):
        layout = catalog.layout(spec.layout_index)
        with column, st.container(border=True):
            st.markdown(f"**{layout.name if layout else spec.layout_index}**")
            st.caption(spec.rationale or "—")
            if layout:
                for placeholder in layout.content_placeholders:
                    text = spec.text_for(placeholder.idx)
                    if text:
                        st.markdown(f"*{placeholder.label}*")
                        st.text(text[:280] + ("…" if len(text) > 280 else ""))
            if st.button(
                "Select", key=f"select_{position}", use_container_width=True
            ):
                st.session_state.chosen = position

    chosen = st.session_state.get("chosen")
    if chosen is None:
        st.info("Pick a candidate to edit and download it.")
        return

    st.divider()
    spec = candidates[chosen]
    layout = catalog.layout(spec.layout_index)
    st.subheader(f"Edit — {layout.name if layout else spec.layout_index}")

    edited = _edited_spec(spec, catalog)

    errors = validate_spec(edited, catalog)
    if errors:
        for error in errors:
            st.error(error)
        return

    for warning in check_specs([edited], catalog):
        st.warning(warning.message)

    try:
        data = render([edited], catalog, presentation)
    except Exception as exc:  # noqa: BLE001
        logger.exception("render failed")
        st.error(f"Could not build the file: {exc}")
        return

    st.download_button(
        "Download .pptx",
        data=data,
        file_name="slidegen.pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        type="primary",
    )


main()
