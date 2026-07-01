(function () {
    // ── Config ─────────────────────────────────────────────────────────────
    var DISPLAY_REFRESH_MS = 5000;
    var LOGO_DURATION_MS   = 4000;
    var MADLIB_DURATION_MS = 6000;
    var LB_DURATION_MS     = 18000;  // long enough to scroll the full board
    var FADE_MS            = 400;
    var WIN_CELEBRATION_MS = 6000;   // how long the celebration stays up
    var LOSS_REVEAL_MS     = 8000;   // how long the answer stays up after a loss

    // ── Madlib data ────────────────────────────────────────────────────────
    var madlibs = (window.__DISPLAY__ && window.__DISPLAY__.madlibs) || [];
    var madlibIndex = 0;

    function nextMadlib() {
        if (!madlibs || madlibs.length === 0) return;
        madlibIndex = (madlibIndex + 1) % madlibs.length;
        var area = document.getElementById('madlib-area');
        area.style.opacity = '0';
        setTimeout(function () {
            document.getElementById('madlib-noun').textContent = madlibs[madlibIndex][0];
            document.getElementById('madlib-verb').textContent = madlibs[madlibIndex][1];
            area.style.opacity = '1';
        }, FADE_MS);
    }

    // Seed first madlib from data if available
    if (madlibs && madlibs.length > 0) {
        document.getElementById('madlib-noun').textContent = madlibs[0][0];
        document.getElementById('madlib-verb').textContent = madlibs[0][1];
    }

    // ── Attract mode cycle ─────────────────────────────────────────────────
    var PANELS = ['logo-panel', 'madlib-panel', 'leaderboard-panel'];
    var DURATIONS = [LOGO_DURATION_MS, MADLIB_DURATION_MS, LB_DURATION_MS];
    var currentPanel = 0;
    var attractTimer = null;
    var madlibCycleTimer = null;

    // One-shot auto-scroll for the leaderboard list (only if it overflows).
    // It must reach the bottom before the attract mode advances off this panel.
    var LB_SCROLL_SPEED = 140;  // px per second when natural timing fits
    var LB_SCROLL_MIN_SECONDS = 7;
    var LB_SCROLL_END_PADDING_SECONDS = 1;

    function stopLeaderboardScroll() {
        var list = document.getElementById('lb-list');
        if (list) list.classList.remove('lb-scrolling');
    }

    function startLeaderboardScroll() {
        var viewport = document.getElementById('lb-viewport');
        var list = document.getElementById('lb-list');
        if (!viewport || !list) return;
        list.classList.remove('lb-scrolling');
        var overflow = viewport.scrollHeight - viewport.clientHeight;
        if (overflow <= 1) return;  // everything already fits — no scroll needed
        list.style.setProperty('--lb-shift', '-' + overflow + 'px');
        var panelSeconds = Math.max(
            LB_SCROLL_MIN_SECONDS,
            (LB_DURATION_MS / 1000) - LB_SCROLL_END_PADDING_SECONDS
        );
        var naturalSeconds = Math.round(overflow / LB_SCROLL_SPEED) + 2;
        var durationSeconds = Math.min(
            panelSeconds,
            Math.max(LB_SCROLL_MIN_SECONDS, naturalSeconds)
        );
        list.style.setProperty('--lb-duration', durationSeconds + 's');
        void list.offsetWidth;  // force reflow so the animation restarts from the top
        list.classList.add('lb-scrolling');
    }

    function showPanel(index) {
        PANELS.forEach(function (id, panelIndex) {
            var el = document.getElementById(id);
            el.classList.toggle('active', panelIndex === index);
        });
        for (var dotIndex = 0; dotIndex < 3; dotIndex++) {
            var dot = document.getElementById('dot-' + dotIndex);
            if (dot) dot.classList.toggle('active', dotIndex === index);
        }
        currentPanel = index;
        // Auto-scroll only while the leaderboard panel is the active one.
        if (index === 2) startLeaderboardScroll();
        else stopLeaderboardScroll();
    }

    // ── Live leaderboard ─────────────────────────────────────────────────────
    function escapeHtml(value) {
        var div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function applyLeaderboard(data) {
        if (!data) return;
        if (data.madlibs && data.madlibs.length) madlibs = data.madlibs;
        var list = document.getElementById('lb-list');
        if (!list || !data.entries) return;
        list.innerHTML = data.entries.map(function (entry, index) {
            var rank = index + 1;
            var medal = rank === 1 ? '🥇' : rank === 2 ? '🥈'
                : rank === 3 ? '🥉' : (rank + '.');
            var rankClass = rank === 1 ? ' lb-rank--first'
                : (rank === 2 || rank === 3) ? ' lb-rank--podium'
                : ' lb-rank--standard';
            var words = entry.guesses + ' word' + (entry.guesses === 1 ? '' : 's');
            return '<div class="lb-row' + (rank <= 3 ? ' top' : '') + '">'
                + '<span class="lb-rank' + rankClass + '">' + medal + '</span>'
                + '<span class="lb-name">' + escapeHtml(entry.player_name) + '</span>'
                + '<span class="lb-guesses">' + words + '</span>'
                + '<span class="lb-time">' + escapeHtml(entry.elapsed_formatted) + '</span>'
                + '</div>';
        }).join('');
        if (currentPanel === 2) startLeaderboardScroll();  // re-arm for new content
    }

    function advanceAttract() {
        var nextPanel = (currentPanel + 1) % PANELS.length;
        showPanel(nextPanel);
        if (nextPanel === 1) nextMadlib(); // refresh madlib on each visit
        attractTimer = setTimeout(advanceAttract, DURATIONS[nextPanel]);
    }

    function startAttract() {
        document.getElementById('attract').classList.remove('hidden');
        document.getElementById('game-mode').classList.add('hidden');
        showPanel(0);
        if (attractTimer) clearTimeout(attractTimer);
        attractTimer = setTimeout(advanceAttract, DURATIONS[0]);
        // Madlib inner cycle — swap phrase every MADLIB_DURATION_MS while in madlib panel
        if (madlibCycleTimer) clearInterval(madlibCycleTimer);
        madlibCycleTimer = setInterval(function () {
            if (currentPanel === 1) nextMadlib();
        }, MADLIB_DURATION_MS);
    }

    function stopAttract() {
        if (attractTimer) { clearTimeout(attractTimer); attractTimer = null; }
        if (madlibCycleTimer) { clearInterval(madlibCycleTimer); madlibCycleTimer = null; }
        stopLeaderboardScroll();
        document.getElementById('attract').classList.add('hidden');
    }

    // ── Game mode ──────────────────────────────────────────────────────────
    var activeWorkflowId = null;
    var extractTimer = null;
    var SVG_EXTRACT_MS = 1500;  // re-clone the live timeline SVG on this cadence
    var TIMELINE_CACHE_KEY = 'durable-wordle:first-timeline-svg';
    var firstTimelineSvgMarkup = null;

    // ── Teaching captions ────────────────────────────────────────────────────
    // Rotating one-liners that explain the Temporal concepts the live timeline is
    // showing. Game mode only — started in showGameMode, stopped in stopGameMode.
    var TEACH_CAPTIONS = [
        'select_word runs as a Temporal Activity',
        'Activities are automatically retried on failure',
        'Human-in-the-loop: the workflow waits for your guess',
        'While waiting for input, the workflow uses no resources',
        'Each guess is a durable update to the workflow',
        'An update validates your guess before it runs',
        'Queries read game state without changing it',
        'The workflow IS the state — no database',
        'Durable Execution: state survives worker restarts',
        'Every step is recorded in the Event History'
    ];
    var CAPTION_INTERVAL_MS = 5000;
    var captionTimer = null;
    var captionIndex = 0;

    function setCaption(index) {
        var el = document.getElementById('timeline-caption');
        if (!el) return;
        el.style.opacity = '0';
        setTimeout(function () {
            el.textContent = TEACH_CAPTIONS[index];
            el.style.opacity = '1';
        }, FADE_MS);
    }

    function startCaptions() {
        var el = document.getElementById('timeline-caption');
        if (!el) return;
        captionIndex = 0;
        el.textContent = TEACH_CAPTIONS[0];
        el.style.opacity = '1';
        if (captionTimer) clearInterval(captionTimer);
        captionTimer = setInterval(function () {
            captionIndex = (captionIndex + 1) % TEACH_CAPTIONS.length;
            setCaption(captionIndex);
        }, CAPTION_INTERVAL_MS);
    }

    function stopCaptions() {
        if (captionTimer) { clearInterval(captionTimer); captionTimer = null; }
        var el = document.getElementById('timeline-caption');
        if (el) el.style.opacity = '0';
    }

    // SVG presentation properties to freeze inline when extracting, so the
    // timeline keeps its colors after leaving the Temporal stylesheet context.
    var SVG_STYLE_PROPS = [
        'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity',
        'stroke-dasharray', 'stroke-linecap', 'opacity', 'color',
        'font-family', 'font-size', 'font-weight', 'text-anchor',
        'dominant-baseline'
    ];

    function inlineComputedStyles(source, clone) {
        var view = source.ownerDocument.defaultView;
        var cs = view.getComputedStyle(source);
        var style = '';
        for (var propIndex = 0; propIndex < SVG_STYLE_PROPS.length; propIndex++) {
            var prop = SVG_STYLE_PROPS[propIndex];
            var val = cs.getPropertyValue(prop);
            if (val) style += prop + ':' + val + ';';
        }
        clone.setAttribute('style', style);
        var sourceKids = source.children, cloneKids = clone.children;
        for (var kidIndex = 0; kidIndex < sourceKids.length; kidIndex++) {
            if (cloneKids[kidIndex]) {
                inlineComputedStyles(sourceKids[kidIndex], cloneKids[kidIndex]);
            }
        }
    }

    function findTimelineSvg(doc) {
        // The timeline graph is by far the largest SVG on the page.
        var svgs = doc.querySelectorAll('svg');
        var best = null, bestArea = 0;
        for (var svgIndex = 0; svgIndex < svgs.length; svgIndex++) {
            var rect = svgs[svgIndex].getBoundingClientRect();
            var area = rect.width * rect.height;
            if (area > bestArea) { bestArea = area; best = svgs[svgIndex]; }
        }
        // Require a meaningfully large SVG so we don't grab an icon
        return bestArea > 40000 ? best : null;
    }

    function readCachedTimelineSvgMarkup() {
        if (firstTimelineSvgMarkup) return firstTimelineSvgMarkup;
        try {
            firstTimelineSvgMarkup = window.sessionStorage.getItem(TIMELINE_CACHE_KEY);
        } catch (err) {
            firstTimelineSvgMarkup = null;
        }
        return firstTimelineSvgMarkup;
    }

    function cacheFirstTimelineSvg(svg) {
        if (firstTimelineSvgMarkup) return;
        firstTimelineSvgMarkup = svg.outerHTML;
        try {
            window.sessionStorage.setItem(TIMELINE_CACHE_KEY, firstTimelineSvgMarkup);
        } catch (err) {
            // Session storage is a nice-to-have; the in-memory copy still helps.
        }
    }

    function buildTimelinePlaceholder() {
        var cachedMarkup = readCachedTimelineSvgMarkup();
        if (cachedMarkup) {
            var template = document.createElement('template');
            template.innerHTML = cachedMarkup.trim();
            var cachedSvg = template.content.firstElementChild;
            if (cachedSvg && cachedSvg.tagName.toLowerCase() === 'svg') {
                return cachedSvg;
            }
        }
        return Object.assign(document.createElement('span'), {
            id: 'timeline-waiting', textContent: 'Loading timeline…'
        });
    }

    function extractTimeline() {
        var frame = document.getElementById('timeline-frame');
        var box = document.getElementById('timeline-box');
        if (!frame || !box) return;
        var doc;
        try { doc = frame.contentDocument; } catch (e) { return; }
        if (!doc) return;
        var svg = findTimelineSvg(doc);
        if (!svg) return;  // not rendered yet — keep "Loading…" and retry
        var clone = svg.cloneNode(true);
        inlineComputedStyles(svg, clone);
        enlargeEventIcons(clone);
        clone.removeAttribute('class');
        clone.style.width = '100%';
        clone.style.height = 'auto';
        cacheFirstTimelineSvg(clone);
        box.replaceChildren(clone);
    }

    // The event-type glyphs are tiny nested <svg>s (9px in a 1138-wide viewBox),
    // so they vanish once the timeline is scaled to fit. Enlarge each in place,
    // keeping it centered on its green marker square, so the symbols read clearly.
    var ICON_SCALE = 2.0;

    function enlargeEventIcons(svgRoot) {
        var icons = svgRoot.querySelectorAll('svg');
        for (var iconIndex = 0; iconIndex < icons.length; iconIndex++) {
            var icon = icons[iconIndex];
            var width = parseFloat(icon.getAttribute('width'));
            var height = parseFloat(icon.getAttribute('height'));
            if (!width || !height || width > 14) continue;  // only the small glyphs
            var x = parseFloat(icon.getAttribute('x')) || 0;
            var y = parseFloat(icon.getAttribute('y')) || 0;
            var centerX = x + width / 2;
            var centerY = y + height / 2;
            var newWidth = width * ICON_SCALE;
            var newHeight = height * ICON_SCALE;
            icon.setAttribute('width', newWidth);
            icon.setAttribute('height', newHeight);
            icon.setAttribute('x', centerX - newWidth / 2);
            icon.setAttribute('y', centerY - newHeight / 2);
        }
    }

    function showGameMode(workflowId, runId) {
        stopAttract();
        hideCelebration();  // a new live game preempts any lingering celebration
        hideLossReveal();
        document.getElementById('game-mode').classList.remove('hidden');
        document.getElementById('timeline-box').replaceChildren(buildTimelinePlaceholder());
        var frame = document.getElementById('timeline-frame');
        var url = '/temporal-ui/namespaces/default/workflows/' + encodeURIComponent(workflowId) + '/' + encodeURIComponent(runId) + '/timeline';
        if (frame.src !== window.location.origin + url) frame.src = url;
        activeWorkflowId = workflowId;
        // Start the extraction loop (the Temporal UI auto-refreshes the source SVG)
        if (extractTimer) clearInterval(extractTimer);
        extractTimer = setInterval(extractTimeline, SVG_EXTRACT_MS);
        startCaptions();
    }

    function stopGameMode() {
        if (extractTimer) { clearInterval(extractTimer); extractTimer = null; }
        stopCaptions();
        var frame = document.getElementById('timeline-frame');
        if (frame) frame.src = 'about:blank';
        document.getElementById('game-mode').classList.add('hidden');
    }

    // ── Win celebration ──────────────────────────────────────────────────────
    var lastWinKey = null;      // submitted_at of the most recently seen win
    var celebrating = false;
    var celebrationTimer = null;

    var CONFETTI_COLORS = ['#cfff0d', '#cacbf9', '#f8fafc'];

    function confettiBurst() {
        var field = document.getElementById('celebration-confetti');
        if (!field) return;
        field.replaceChildren();
        for (var pieceIndex = 0; pieceIndex < 80; pieceIndex++) {
            var piece = document.createElement('div');
            piece.className = 'confetti';
            var angle = Math.random() * Math.PI * 2;
            var distance = 28 + Math.random() * 42;  // vmin from center
            piece.style.setProperty('--dx', (Math.cos(angle) * distance) + 'vmin');
            piece.style.setProperty('--dy', (Math.sin(angle) * distance) + 'vmin');
            var size = 6 + Math.random() * 8;
            piece.style.width = size + 'px';
            piece.style.height = size + 'px';
            piece.style.background = CONFETTI_COLORS[pieceIndex % CONFETTI_COLORS.length];
            piece.style.animationDelay = (Math.random() * 0.3) + 's';
            field.appendChild(piece);
        }
    }

    function ordinalRank(rank) {
        if (rank === 1) return '🥇 1ST PLACE';
        if (rank === 2) return '🥈 2ND PLACE';
        if (rank === 3) return '🥉 3RD PLACE';
        return 'RANKED #' + rank;
    }

    function hideCelebration() {
        if (celebrationTimer) { clearTimeout(celebrationTimer); celebrationTimer = null; }
        celebrating = false;
        document.getElementById('celebration').classList.add('hidden');
    }

    function celebrateWin(win) {
        celebrating = true;
        var name = (win.player_name || 'Someone').toUpperCase();
        var guessWord = win.guesses === 1 ? 'guess' : 'guesses';
        document.getElementById('celeb-name').textContent = name;
        document.getElementById('celeb-detail').textContent =
            'solved in ' + win.guesses + ' ' + guessWord + ' · ' + win.elapsed_formatted;
        document.getElementById('celeb-rank').textContent = ordinalRank(win.rank);
        document.getElementById('celebration').classList.remove('hidden');
        confettiBurst();
        if (celebrationTimer) clearTimeout(celebrationTimer);
        celebrationTimer = setTimeout(hideCelebration, WIN_CELEBRATION_MS);
    }

    function applyWin(win, suppressCelebration) {
        if (!win) return;
        var key = win.submitted_at;
        if (key === lastWinKey) return;  // already seen — fire once
        lastWinKey = key;
        if (!suppressCelebration && !celebrating && !isGameMode) celebrateWin(win);
    }

    // ── Loss reveal ─────────────────────────────────────────────────────────
    var lastLossKey = null;
    var lossTimer = null;

    function hideLossReveal() {
        if (lossTimer) { clearTimeout(lossTimer); lossTimer = null; }
        var panel = document.getElementById('loss-reveal');
        if (panel) panel.classList.add('hidden');
    }

    function revealLoss(loss) {
        var word = String(loss.target_word || '').toUpperCase();
        if (!word) return;
        var wordEl = document.getElementById('loss-word');
        if (!wordEl) return;
        wordEl.replaceChildren();
        word.split('').forEach(function (letter) {
            var tile = document.createElement('span');
            tile.className = 'loss-letter';
            tile.textContent = letter;
            wordEl.appendChild(tile);
        });
        var detail = document.getElementById('loss-detail');
        if (detail) {
            if (loss.status === 'abandoned') {
                detail.textContent = 'Time expired before the next guess';
            } else {
                detail.textContent = 'Solved by the workflow after ' + loss.guesses + ' guesses';
            }
        }
        document.getElementById('loss-reveal').classList.remove('hidden');
        if (lossTimer) clearTimeout(lossTimer);
        lossTimer = setTimeout(hideLossReveal, LOSS_REVEAL_MS);
    }

    function applyLoss(loss, suppressReveal) {
        if (!loss) return;
        var key = loss.workflow_id + ':' + loss.run_id + ':lost';
        if (key === lastLossKey) return;
        lastLossKey = key;
        if (!suppressReveal && !isGameMode) revealLoss(loss);
    }

    // ── Polling ────────────────────────────────────────────────────────────
    var isGameMode = false;
    var seenInitialDisplayState = false;

    function applyActiveGame(data) {
        if (data && data.workflow_id && data.run_id) {
            if (!isGameMode || data.workflow_id !== activeWorkflowId) {
                isGameMode = true;
                showGameMode(data.workflow_id, data.run_id);
            }
        } else if (isGameMode) {
            isGameMode = false;
            activeWorkflowId = null;
            stopGameMode();
            startAttract();
        }
    }

    function pollDisplayState() {
        fetch('/api/display-state')
            .then(function (res) { return res.json(); })
            .then(function (data) {
                applyLeaderboard(data.leaderboard);
                applyActiveGame(data.active_game);
                applyWin(data.win, !seenInitialDisplayState);
                applyLoss(data.loss, !seenInitialDisplayState);
                seenInitialDisplayState = true;
            })
            .catch(function () { /* silently ignore poll errors */ });
    }

    // ── Particles ──────────────────────────────────────────────────────────
    var container = document.getElementById('particles');
    for (var particleIndex = 0; particleIndex < 60; particleIndex++) {
        var particle = document.createElement('div');
        particle.className = 'particle';
        var size = Math.random() * 5 + 1.5;
        particle.style.width  = size + 'px';
        particle.style.height = size + 'px';
        particle.style.left   = Math.random() * 100 + 'vw';
        particle.style.animationDuration = (Math.random() * 12 + 6) + 's';
        particle.style.animationDelay    = (Math.random() * 12) + 's';
        particle.style.opacity = String(Math.random() * 0.5 + 0.1);
        // Larger motes get a soft grellow glow for depth on the spinning fan.
        if (size > 4) particle.style.boxShadow = '0 0 ' + (size * 2) + 'px #cfff0d';
        container.appendChild(particle);
    }

    // ── Calibration ─────────────────────────────────────────────────────────
    var CAL_KEY = 'boothCalibration';
    var cal = { shiftX: 0, shiftY: 0, circleW: 68 };  // circleW in vmin
    var displayStarted = false;

    function applyCalibration() {
        var root = document.body.style;
        root.setProperty('--shift-x', cal.shiftX + 'px');
        root.setProperty('--shift-y', cal.shiftY + 'px');
        root.setProperty('--circle-w', cal.circleW + 'vmin');
        var readout = document.getElementById('cal-readout');
        if (readout) {
            readout.textContent = 'x:' + cal.shiftX + 'px · y:' + cal.shiftY
                + 'px · w:' + cal.circleW + 'vmin';
        }
    }

    function loadCalibration() {
        try {
            var saved = JSON.parse(localStorage.getItem(CAL_KEY));
            if (saved && typeof saved.shiftX === 'number') {
                cal.shiftX = saved.shiftX;
                cal.shiftY = saved.shiftY;
                cal.circleW = saved.circleW;
            }
        } catch (e) { /* ignore corrupt/missing */ }
    }

    function nudge(dir) {
        var STEP = 5;
        if (dir === 'left') cal.shiftX -= STEP;
        else if (dir === 'right') cal.shiftX += STEP;
        else if (dir === 'up') cal.shiftY -= STEP;
        else if (dir === 'down') cal.shiftY += STEP;
        else if (dir === 'center') { cal.shiftX = 0; cal.shiftY = 0; }
        applyCalibration();
    }

    function resizeCircle(delta) {
        cal.circleW = Math.max(30, Math.min(100, cal.circleW + delta));
        applyCalibration();
    }

    function startDisplay() {
        if (displayStarted) return;
        displayStarted = true;
        startAttract();
        pollDisplayState();
        setInterval(pollDisplayState, DISPLAY_REFRESH_MS);
    }

    function saveAndLaunch() {
        try { localStorage.setItem(CAL_KEY, JSON.stringify(cal)); } catch (e) { /* ignore */ }
        document.getElementById('calibration').classList.add('hidden');
        startDisplay();
    }

    function initCalibration() {
        loadCalibration();
        applyCalibration();
        var overlay = document.getElementById('calibration');
        overlay.classList.remove('hidden');

        overlay.querySelectorAll('[data-nudge]').forEach(function (btn) {
            btn.addEventListener('click', function () { nudge(btn.getAttribute('data-nudge')); });
        });
        overlay.querySelectorAll('[data-width]').forEach(function (btn) {
            btn.addEventListener('click', function () { resizeCircle(parseInt(btn.getAttribute('data-width'), 10)); });
        });
        document.getElementById('cal-save').addEventListener('click', saveAndLaunch);

        document.addEventListener('keydown', function (event) {
            if (displayStarted) return;  // only while calibrating
            if (event.key === 'ArrowLeft') { event.preventDefault(); nudge('left'); }
            else if (event.key === 'ArrowRight') { event.preventDefault(); nudge('right'); }
            else if (event.key === 'ArrowUp') { event.preventDefault(); nudge('up'); }
            else if (event.key === 'ArrowDown') { event.preventDefault(); nudge('down'); }
            else if (event.key === '[') { resizeCircle(-1); }
            else if (event.key === ']') { resizeCircle(1); }
            else if (event.key === 'Enter') { saveAndLaunch(); }
        });
    }

    // ── Boot ───────────────────────────────────────────────────────────────
    initCalibration();  // calibrate first → Save & Launch enters display mode
})();
