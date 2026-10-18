/* Stage 8 response rendering: one controller per assistant container. */
(() => {
    const states = new WeakMap();
    const HOVER_DELAY = 850;
    const MAX_HIGHLIGHT_CHARS = 50000;
    const escapeHtml = text => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const touchLayout = matchMedia("(hover: none), (pointer: coarse)");

    function textNodes(content) {
        const walker = document.createTreeWalker(content,NodeFilter.SHOW_TEXT), result=[];
        while(walker.nextNode()) if(!walker.currentNode.parentElement.closest("button,.code-toolbar")) result.push(walker.currentNode);
        return result;
    }
    function selectedOffsets(content) {
        const selection=getSelection();
        if(!selection || selection.isCollapsed || !content.contains(selection.anchorNode) || !content.contains(selection.focusNode))return null;
        let offset=0,anchor=null,focus=null;
        for(const node of textNodes(content)) {
            if(node===selection.anchorNode)anchor=offset+selection.anchorOffset;
            if(node===selection.focusNode)focus=offset+selection.focusOffset;
            offset+=node.textContent.length;
        }
        return anchor===null || focus===null ? null : {anchor,focus};
    }
    function restoreSelection(content,saved) {
        if(!saved)return;
        let offset=0,anchor,focus;
        for(const node of textNodes(content)) {
            const end=offset+node.textContent.length;
            if(!anchor && saved.anchor>=offset && saved.anchor<=end)anchor=[node,saved.anchor-offset];
            if(!focus && saved.focus>=offset && saved.focus<=end)focus=[node,saved.focus-offset];
            offset=end;
        }
        if(anchor && focus)getSelection().setBaseAndExtent(...anchor,...focus);
    }

    function attach(content) {
        if (states.has(content)) return states.get(content);
        const state = { frame: null, cache: new Map(), components: new Map() };
        states.set(content, state);
        content.classList.add("response-body");
        state.measure = () => content.querySelectorAll("[data-component-key]").forEach(component => {
            const overflow = component.scrollHeight > component.clientHeight + 1 || component.scrollWidth > component.clientWidth + 1;
            component.tabIndex = overflow ? 0 : -1;
            const button = component.parentElement.querySelector(".expand-component");
            if (button) button.hidden = !overflow && !component.classList.contains("component-expanded");
        });
        state.observer = new ResizeObserver(state.measure);
        state.observer.observe(content);
        return state;
    }

    function decorateComponents(content, state) {
        content.querySelectorAll("table").forEach(table => {
            const wrapper = document.createElement("div"), viewport = document.createElement("div");
            wrapper.className = "table-component"; viewport.className = "table-viewport";
            table.replaceWith(wrapper); wrapper.append(viewport); viewport.append(table);
        });
        content.querySelectorAll(".code-block pre, .table-viewport").forEach((component,index) => {
            const key = String(index); component.dataset.componentKey = key;
            const saved = state.components.get(key) || {};
            component.classList.add("response-component");
            component.classList.toggle("component-expanded",Boolean(saved.expanded));
            component.setAttribute("role","region");
            component.setAttribute("aria-label",component.tagName === "PRE" ? "Code or structured data" : "Table");
            const button = document.createElement("button"); button.type = "button"; button.className = "expand-component";
            const update = () => { button.textContent = component.classList.contains("component-expanded") ? "Collapse" : "Expand"; button.setAttribute("aria-expanded",String(component.classList.contains("component-expanded"))); };
            button.addEventListener("click",() => {component.classList.toggle("component-expanded");update();state.measure();});
            if (component.tagName === "PRE") component.parentElement.querySelector(".code-toolbar").append(button);
            else component.parentElement.prepend(button);
            update(); component.scrollTop = saved.top || 0; component.scrollLeft = saved.left || 0;
            const reading = () => content.dispatchEvent(new CustomEvent("response-reading",{bubbles:true}));
            component.addEventListener("wheel",reading,{passive:true});
            component.addEventListener("touchstart",reading,{passive:true});
            component.addEventListener("keydown",event=>{if(["ArrowUp","ArrowDown","PageUp","PageDown","Home","End"].includes(event.key))reading();});
            if (saved.focused) component.focus({preventScroll:true});
        });
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
        const selection=selectedOffsets(content);
        const scrollTop = content.scrollTop;
        content.querySelectorAll("[data-component-key]").forEach(component => state.components.set(component.dataset.componentKey,{
            top:component.scrollTop,left:component.scrollLeft,expanded:component.classList.contains("component-expanded"),focused:document.activeElement===component}));
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
            decorateComponents(content, state);
        } catch {
            content.classList.remove("markdown-content");
            content.textContent = markdown;
        }
        content.scrollTop = scrollTop;
        restoreSelection(content,selection);
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
        if (latest) latest.querySelectorAll(".response-component").forEach(c => { c.scrollTop = c.scrollHeight; });
    }
    window.ResponseUX = { render, dispose, toLatest, HOVER_DELAY };
})();
