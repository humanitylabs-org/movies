from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "web" / "index.html").read_text()
APP = (ROOT / "web" / "app.js").read_text()
CSS = (ROOT / "web" / "styles.css").read_text()


def test_home_chrome_is_minimal_and_named_cinequeue():
    assert "Movies" in INDEX
    assert "Cinecue" not in INDEX
    assert "Fast scan watchlist" not in INDEX
    assert "compact tailnet watchlist" not in INDEX
    assert "Live on tailnet" not in APP
    assert "id=\"addForm\"" not in INDEX
    assert "class=\"stats\"" not in INDEX


def test_home_filters_are_unwatched_watched_and_genre_chips():
    assert 'data-filter="all"' not in INDEX
    assert 'data-filter="unwatched"' in INDEX
    assert 'data-filter="watched"' in INDEX
    assert "let filter = 'unwatched'" in APP
    assert "genreFilter" not in INDEX
    assert 'id="genreChips"' in INDEX
    assert "selectedGenres" in APP
    assert "toggleGenre" in APP


def test_movie_rows_do_not_render_letterboxd_badge():
    assert 'class="lb"' not in INDEX
    assert "node.querySelector('.lb')" not in APP
    assert "LB</a>" not in INDEX


def test_detail_mark_watched_is_separate_from_external_links():
    assert "detail-links" in APP
    assert "watch-section" in APP
    assert ".watch-section" in CSS
    assert ".detail-watch" in CSS


def test_detail_loads_where_to_watch_for_the_configured_region():
    server = (ROOT / "cinequeue" / "server.py").read_text()
    assert "Where to watch" in APP
    assert "watchRegionName()" in APP
    assert "appConfig.watch?.enabled" in APP
    assert "JustWatch" in APP
    assert "/api/watch-providers/" in APP
    assert "where-to-watch" in APP
    assert ".where-to-watch" in CSS
    assert "/api/watch-providers/" in server
    assert APP.index("where-to-watch") < APP.index("watch-section")


def test_detail_can_bump_movie_to_top_of_queue():
    assert "queue-section" not in APP
    assert "detail-bump" in APP
    assert "/api/bump/" in APP
    assert "Bump to top" in APP
    assert "${bump}" in APP
    assert APP.index("${lb}") < APP.index("${bump}") < APP.index("watch-section")
    assert ".queue-section" not in CSS
    assert ".detail-links button" in CSS


def test_add_control_is_a_compact_header_button_not_a_fab():
    assert "addRequestFab" not in APP
    assert "addRequestFab" not in INDEX
    assert "add-request-fab" not in INDEX
    assert "add-request-fab" not in CSS
    assert 'id="addOpen"' in INDEX
    assert "addOpen" in APP
    assert ".add-open" in CSS


def test_view_toggle_group_present_with_persisted_state():
    assert 'class="viewtoggle"' in INDEX
    assert 'data-view="list"' in INDEX
    assert 'data-view="grid"' in INDEX
    assert 'aria-label="List view"' in INDEX
    assert 'aria-label="Poster view"' in INDEX
    assert "cinequeue.view" in APP
    assert "aria-pressed" in APP


def test_poster_grid_is_exactly_three_columns_on_phone():
    assert "repeat(3, minmax(0,1fr))" in CSS
    # The 3-column rule must live inside a max-width:699px media block.
    grid_pos = CSS.index("repeat(3, minmax(0,1fr))")
    media_pos = CSS.rindex("@media (max-width: 699px)", 0, grid_pos)
    assert media_pos > -1
    assert CSS.index("{", media_pos) < grid_pos
    assert 'data-view="grid"' in CSS.replace("'", '"')


def test_row_template_has_no_action_buttons_and_no_confirm():
    template = INDEX[INDEX.index('id="rowTemplate"'):]
    template = template[:template.index("</template>")]
    assert 'class="watch"' not in template
    assert 'class="delete"' not in template
    assert "confirm(`Remove ${label.toLowerCase()}" in APP
    assert ".actions button" not in CSS


def test_archive_api_used_delete_api_absent_from_frontend():
    assert "/api/delete/" not in APP
    assert "/api/archive/" in APP
    assert "/api/restore/" in APP
    assert "undoBar" in APP
    assert "set_archived" in (ROOT / "cinequeue" / "core.py").read_text()
    assert "/api/archive/" in (ROOT / "cinequeue" / "server.py").read_text()
    assert "/api/restore/" in (ROOT / "cinequeue" / "server.py").read_text()
    # Server keeps /api/delete for compatibility.
    assert "/api/delete/" in (ROOT / "cinequeue" / "server.py").read_text()


def test_swipe_reveal_layers_and_touch_action_present():
    assert "swipe-bg" in INDEX
    assert "swipe-bg" in CSS
    assert "touch-action: pan-y" in CSS
    assert "movie-surface" in INDEX
    assert "movie-surface" in CSS


def test_tile_template_is_poster_only():
    assert 'id="tileTemplate"' in INDEX
    template = INDEX[INDEX.index('id="tileTemplate"'):]
    template = template[:template.index("</template>")]
    assert "<h2" not in template
    assert "rating-badge" not in template
    assert "rank" not in template


def test_sr_status_region_and_list_not_live():
    assert 'id="srStatus"' in INDEX
    list_open_tag = INDEX[INDEX.index('id="list"'):]
    assert 'aria-live' not in list_open_tag[:list_open_tag.index(">")]
    assert "srStatus" in APP


def test_detail_view_has_compact_archive_action():
    assert "detail-archive" in APP
    assert "detail-archive" in CSS


def test_default_sort_is_queue_order_with_date_added_available():
    assert "Letterboxd order" not in INDEX
    assert "Queue order" in INDEX
    assert "Date added" in INDEX
    assert INDEX.index('value="queue-order"') < INDEX.index('value="date-added"')
    assert 'value="date-added"' in INDEX
    assert "sortEl.value === 'queue-order'" in APP
    assert "sortEl.value === 'date-added'" in APP
    assert "return a._index - b._index;" in APP


def test_index_rows_show_color_coded_rating_badge():
    assert 'class="rating-badge"' in INDEX
    assert "ratingClass" in APP
    assert "node.querySelector('.rating-badge')" in APP
    assert ".rating-badge.high" in CSS
    assert ".rating-badge.medium" in CSS
    assert ".rating-badge.low" in CSS
    assert "IMDb ${ratingLabel(value)}" in APP
    assert "IMDb user rating ${ratingLabel(value)} out of 10" in APP
    assert "★ ${ratingLabel(value)}" not in APP


def test_watch_actions_use_checkmark_and_preserve_history_on_requeue():
    assert "○" not in INDEX
    assert "○" not in APP
    assert "✓" in INDEX
    assert "Mark watched" in APP
    assert "Watch again" in APP
    assert "Mark unwatched" not in APP


def test_detail_rating_is_prominent_and_explicitly_imdb_user_sentiment():
    assert "Letterboxd:</b>" not in APP
    assert "rating-spotlight" in APP
    assert "<span>IMDb users</span>" in APP
    assert "/ 10" in APP
    assert "Open IMDb" in APP
    assert "movie.rating_source !== 'imdb'" in APP
    assert APP.index("rating-spotlight") < APP.index("facts")
    assert ".rating-spotlight" in CSS


def test_detail_shows_runtime_minutes_near_top():
    assert "runtime_minutes" in APP
    assert "runtimeLabel" in APP
    assert "detailMetaItems" in APP
    assert "Length not listed" in APP
    assert "detail-meta" not in APP
    assert ".detail-meta" not in CSS
    assert APP.index("detailMetaItems") < APP.index("<h1>")


def test_detail_notes_are_generic_and_episode_highlights_are_structured():
    assert "detailNotesMarkup" in APP
    assert "movie.notes" in APP
    assert "movie.featured_episodes" in APP
    assert "Highlighted episodes" in APP
    assert "S${episode.season}E${episode.episode}" in APP
    assert "${notesMarkup}" in APP
    assert ".detail-notes" in CSS
    assert ".detail-highlights" in CSS


def test_home_rows_include_runtime_next_to_year_and_genre():
    assert "runtimeLabel(movie)" in APP
    assert ": [movie.year || 'Year unknown', (movie.genres || [])[0], runtimeLabel(movie)]" in APP
    assert "watchSummary(movie)" in APP


def test_home_has_agent_add_request_composer_with_direct_add_fallback():
    assert 'id="addRequestPanel"' in INDEX
    assert 'id="addRequestBackdrop"' in INDEX
    assert 'id="addRequestForm"' in INDEX
    assert 'id="addRequestInput"' in INDEX
    assert 'id="addRequestButton"' in INDEX
    assert "new Movies request" in APP
    assert "/api/request-add" in APP
    assert "/api/add'" in APP and "agentReady()" in APP
    assert "openAddRequestPanel" in APP
    assert "closeAddRequestPanel" in APP
    assert "Sent to your Wizard" in APP
    assert "Could not confirm your request" in APP and "Archimedes" not in APP
    assert "'.add-request'" not in APP
    assert ".add-request-panel" in CSS
    assert ".add-request-status.ok" in CSS


def test_queue_numbers_are_absent_from_both_lists_and_details():
    template = INDEX[INDEX.index('id="rowTemplate"'):]
    template = template[:template.index("</template>")]
    assert "rank-cell" not in template
    assert "class=\"rank\"" not in template
    assert "Queue position #" not in APP
    assert "Already #1" not in APP
    assert ".rank-cell" not in CSS


def test_watched_cards_keep_full_contrast():
    assert ".movie.watched .movie-surface" not in CSS
    assert ".tile.watched" not in CSS
    assert "opacity: .55" not in CSS


def test_watched_tab_is_reverse_chronological_history_not_queue_state():
    assert "latest_watched_on" in APP
    assert "compareLatestWatchedNewestFirst" in APP
    assert "filter === 'watched'" in APP
    assert "movie.queued" in APP
    assert 'value="watched-date"' in INDEX
    assert "Recently watched" in INDEX


def test_watch_history_dates_are_visible_editable_and_requeueable():
    assert "Watch history" in APP
    assert 'type="date"' in APP
    assert "/api/watch-date/" in APP
    assert "/api/watch-again/" in APP
    assert "Watch again" in APP
    assert "Mark watched again" in APP
    assert "Mark unwatched" not in APP


def test_watch_mutations_are_recoverable_and_repeat_safe():
    assert "/api/watch-delete/" in APP
    assert "kind: 'watch'" in APP
    assert "requeue: true" in APP
    assert "undo available" in APP
    assert "event.repeat" in APP
    assert "actionPending" in APP
    assert "data-watch-delete" in APP


def test_runtime_rating_aria_label_is_dynamic():
    assert "IMDb user rating ${ratingLabel(value)} out of 10" in APP


def test_back_stack_never_exits_on_one_press():
    assert "root-floor" not in APP
    assert "WizardApp?.back.useHistory" in APP
    assert "exitArmedUntil" not in APP
    assert "history.pushState(null" not in APP


def test_frontend_urls_are_relative_and_owner_neutral():
    import re
    for text in (APP, INDEX):
        assert not re.search(r"https?://[a-z0-9.-]+\.(ts\.net|local)\b", text)


def test_request_cards_only_show_actionable_states():
    skin = (ROOT / "web" / "movies-skin.css").read_text()
    assert "requestCardModel" in APP and "renderRequestCard" in APP
    # Completed requests never render a card; the movie itself is the result.
    assert "Added and verified" not in APP
    assert 'data-request-action="answer"' in APP
    assert ".request-choice" in skin


def test_design_rules_fill_window_and_sheet_swipe_close():
    # Design rules v0: the app frame has no width cap, and the add sheet also closes on a swipe down.
    import re
    assert not re.search(r"\.wrap\s*\{[^}]*max-width", CSS)
    assert "addRequestHead.addEventListener('touchend'" in APP
