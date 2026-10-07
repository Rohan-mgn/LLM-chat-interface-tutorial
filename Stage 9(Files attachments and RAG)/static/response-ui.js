/* Stage 8 response rendering: one controller per assistant container. */
(() => {
    const states = new WeakMap();
    const HOVER_DELAY = 850;
    const MAX_HIGHLIGHT_CHARS = 50000;
    const escapeHtml = text => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const touchLayout = matchMedia("(hover: none), (pointer: coarse)");

    function attach(content) {
        if (states.has(content)) return states.get(content);
        const state = { timer: null, frame: null, hovered: false, active: false, keyboard: false, cache: new Map() };
        states.set(content, state);
        content.classList.add("response-body");
        const hint = document.createElement("button");
        hint.type = "button";
        hint.className = "response-scroll-hint";
        hint.hidden = true;
        content.after(hint);
        state.hint = hint;
        const reading = () => content.dispatchEvent(new CustomEvent("response-reading", { bubbles: true }));
        function activate(value) {
            state.active = Boolean(value && content.classList.contains("response-overflow"));
            content.classList.toggle("inner-scroll-active", state.active);
            hint.textContent = state.active || touchLayout.matches
                ? "Scrollable reply · scroll here to read more"
                : "Long reply · hover to scroll, or select here";
            hint.setAttribute("aria-label", "Focus this long reply to scroll with the keyboard");
        }
        state.measure = () => {
            if (!content.isConnected) return;
            const wasOverflowing = content.classList.contains("response-overflow");
            const overflow = content.scrollHeight > content.clientHeight + 1;
            if (overflow && !wasOverflowing && state.hovered && !touchLayout.matches) {
                clearTimeout(state.timer);
                state.timer = setTimeout(() => {
                    if (state.hovered && content.isConnected) activate(true);
                }, HOVER_DELAY);
            }
            content.classList.toggle("response-overflow", overflow);
            hint.hidden = !overflow;
            if (overflow) {
                content.tabIndex = 0;
                content.setAttribute("role", "region");
                content.setAttribute("aria-label", "Long assistant reply. Use arrow keys or Page Down to read. Escape returns scrolling to the conversation.");
            } else {
                content.removeAttribute("tabindex");
                content.removeAttribute("role");
                content.removeAttribute("aria-label");
            }
            activate(overflow && (state.active || state.keyboard));
        };
        content.addEventListener("pointerenter", event => {
            state.hovered = true;
            if (event.pointerType === "touch" || touchLayout.matches || !content.classList.contains("response-overflow")) return;
            clearTimeout(state.timer);
            state.timer = setTimeout(() => {
                if (state.hovered && content.isConnected) activate(true);
            }, HOVER_DELAY);
        });
        content.addEventListener("pointerleave", () => {
            state.hovered = false;
            clearTimeout(state.timer);
            activate(false);
        });
        content.addEventListener("focusin", () => { state.keyboard = true; activate(true); });
        content.addEventListener("focusout", event => {
            if (!content.contains(event.relatedTarget)) { state.keyboard = false; activate(false); }
        });
        content.addEventListener("keydown", event => {
            if (event.key === "Escape") {
                clearTimeout(state.timer);
                state.keyboard = false;
                activate(false);
                document.getElementById("conversation").focus({ preventScroll: true });
                return;
            }
            if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) reading();
        });
        content.addEventListener("pointerdown", event => {
            if (event.pointerType === "touch") { activate(true); reading(); }
        });
        content.addEventListener("touchstart", () => { activate(true); reading(); }, { passive: true });
        content.addEventListener("wheel", event => {
            // Horizontal gestures and Shift+wheel belong to code/table scrollers.
            if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
            if (!content.classList.contains("response-overflow")) return;
            const target = state.active || touchLayout.matches ? content : document.getElementById("conversation");
            const units = event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? target.clientHeight : 1;
            if (event.cancelable) {
                event.preventDefault();
                target.scrollTop += event.deltaY * units;
            }
            if (target === content) reading();
        }, { passive: false });
        hint.addEventListener("click", () => { content.focus({ preventScroll: true }); reading(); });
        state.observer = new ResizeObserver(state.measure);
        state.observer.observe(content);
        state.activate = activate;
        return state;
    }

    function decorateCode(content, state, final, positions) {
        content.querySelectorAll("pre > code").forEach((code, index) => {
            const source = code.textContent;
            const declared = code.className.match(/(?:^|\s)language-([a-z0-9_-]+)/i)?.[1]?.toLowerCase() || "text";
            const aliases = { js: "javascript", py: "python", html: "markup", xml: "markup", sh: "bash", shell: "bash", plaintext: "text", txt: "text" };
            const language = aliases[declared] || declared;
            code.className = "language-" + language;
            if (final && source.length <= MAX_HIGHLIGHT_CHARS && window.Prism?.languages[language]) {
                try {
                    const key = language + "\0" + source;
                    let html = state.cache.get(key);
                    if (html === undefined) {
                        html = Prism.highlight(source, Prism.languages[language], language);
                        if (state.cache.size >= 24) state.cache.clear();
                        state.cache.set(key, html);
                    }
                    code.replaceChildren(DOMPurify.sanitize(html, {
                        ALLOWED_TAGS: ["span"], ALLOWED_ATTR: ["class"],
                        ALLOW_DATA_ATTR: false, RETURN_DOM_FRAGMENT: true
                    }));
                } catch { code.textContent = source; }
            }
            const pre = code.parentElement;
            pre.tabIndex = 0;
            pre.setAttribute("aria-label", declared + " code; scroll horizontally for long lines");
            const block = document.createElement("div");
            block.className = "code-block";
            const toolbar = document.createElement("div");
            toolbar.className = "code-toolbar";
            const label = document.createElement("span");
            label.className = "code-language";
            label.textContent = declared;
            const copy = document.createElement("button");
            copy.type = "button";
            copy.className = "copy-code";
            copy.textContent = "Copy code";
            copy.setAttribute("aria-label", "Copy " + declared + " source code");
            copy.addEventListener("click", async () => {
                try {
                    await navigator.clipboard.writeText(source);
                    copy.textContent = "Copied";
                    copy.setAttribute("aria-label", "Code copied");
                } catch {
                    copy.textContent = "Select to copy";
                    const selection = getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(code);
                    selection.removeAllRanges();
                    selection.addRange(range);
                }
                setTimeout(() => {
                    if (copy.isConnected) {
                        copy.textContent = "Copy code";
                        copy.setAttribute("aria-label", "Copy " + declared + " source code");
                    }
                }, 1800);
            });
            toolbar.append(label, copy);
            pre.replaceWith(block);
            block.append(toolbar, pre);
            pre.scrollLeft = positions[index] || 0;
        });
    }

    function render(content, markdown, { final = true } = {}) {
        const state = attach(content);
        const scrollTop = content.scrollTop;
        const positions = [...content.querySelectorAll("pre")].map(pre => pre.scrollLeft);
        try {
            if (!window.marked?.parse || !window.DOMPurify?.isSupported) throw new Error("Markdown unavailable");
            const renderer = new marked.Renderer();
            renderer.code = ({ text, lang }) => {
                const language = (lang || "").trim().split(/\s+/)[0].toLowerCase();
                const safeLanguage = /^[a-z0-9_-]{1,40}$/.test(language) ? language : "text";
                // Exact parser source: no toolbar text or synthetic newline added.
                return '<pre><code class="language-' + safeLanguage + '">' + escapeHtml(text) + "</code></pre>";
            };
            const html = marked.parse(markdown, { renderer, gfm: true, breaks: false, async: false });
            const fragment = DOMPurify.sanitize(html, {
                ALLOWED_TAGS: ["p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "ul", "ol", "li", "blockquote", "pre", "code", "hr", "table", "thead", "tbody", "tr", "th", "td", "a"],
                ALLOWED_ATTR: ["href", "title", "start", "align", "class"],
                ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false, RETURN_DOM_FRAGMENT: true
            });
            // Only a code language class from Markdown is meaningful here.
            fragment.querySelectorAll("[class]").forEach(element => {
                const language = element.tagName === "CODE" && /^language-[a-z0-9_-]{1,40}$/.test(element.className);
                if (!language) element.removeAttribute("class");
            });
            content.replaceChildren(fragment);
            content.classList.add("markdown-content");
            decorateCode(content, state, final, positions);
        } catch {
            content.classList.remove("markdown-content");
            content.textContent = markdown;
        }
        content.scrollTop = scrollTop;
        state.measure();
        cancelAnimationFrame(state.frame);
        state.frame = requestAnimationFrame(state.measure);
    }

    function dispose(root) {
        root.querySelectorAll(".response-body").forEach(content => {
            const state = states.get(content);
            if (state) {
                clearTimeout(state.timer);
                cancelAnimationFrame(state.frame);
                state.observer.disconnect();
                states.delete(content);
            }
        });
    }
    function toLatest(root) {
        const latest = [...root.querySelectorAll(".response-body")].at(-1);
        if (latest) latest.scrollTop = latest.scrollHeight;
    }
    window.ResponseUX = { render, dispose, toLatest, HOVER_DELAY };
})();