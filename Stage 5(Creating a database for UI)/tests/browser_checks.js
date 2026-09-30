// Exercise the actual page, real fetch requests, and the server's test database.
(async () => {
    const assert = (condition, message) => {
        if (!condition) throw new Error(message);
    };
    const tick = () => new Promise(resolve => setTimeout(resolve, 10));
    const waitFor = async (condition, description) => {
        for (let attempt = 0; attempt < 400; attempt++) {
            if (condition()) return;
            await tick();
        }
        throw new Error("Timed out: " + description);
    };
    const frame = document.getElementById("appFrame");
    const mobile = new URLSearchParams(location.search).has("mobile");
    frame.style.width = mobile ? "390px" : "100%";
    let page;
    let doc;
    let requests = [];
    const report = async result => {
        document.body.dataset.result = JSON.stringify(result);
        await fetch("/__lesson9_result", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(result)
        });
    };
    const state = async () => (await fetch("/__lesson9_state", { cache: "no-store" })).json();
    const userContents = () => [...doc.querySelectorAll(".message-user .message-content")];
    const assistantContents = () => [...doc.querySelectorAll(".message-assistant .message-content")];
    const ready = () => !doc.getElementById("messageInput").disabled;
    const openPage = async () => {
        const loaded = new Promise(resolve => frame.addEventListener("load", resolve, { once: true }));
        frame.src = "/?browser-check=" + Date.now();
        await loaded;
        page = frame.contentWindow;
        doc = frame.contentDocument;
        await waitFor(ready, "startup history load");
        const originalFetch = page.fetch.bind(page);
        requests = [];
        page.fetch = (url, options = {}) => {
            requests.push({ url, method: options.method || "GET", body: options.body });
            return originalFetch(url, options);
        };
    };
    const setInput = value => {
        doc.getElementById("messageInput").value = value;
        doc.getElementById("messageInput").dispatchEvent(new page.Event("input", { bubbles: true }));
    };
    const checkMarkdown = content => {
        assert(content.querySelector("h1")?.textContent === "Markdown demo", "Heading is missing");
        assert(content.querySelector("strong")?.textContent === "Bold text", "Bold is missing");
        assert(content.querySelectorAll("ul > li").length === 2, "Bullet list is missing");
        assert(content.querySelectorAll("ol > li").length === 2, "Ordered list is missing");
        assert(content.querySelectorAll("table tbody tr").length === 2, "Markdown table is missing");
        assert(content.querySelector("pre code")?.textContent.includes('print("Hello, XYZ!")'), "Fenced code is missing");
        assert(content.querySelector("pre code").textContent.includes("<script>alert(1)</script>"), "Code was interpreted as HTML");
        assert(content.querySelector("p code")?.textContent === "print()", "Inline code is missing");
        assert(content.querySelector("blockquote"), "Blockquote is missing");
        assert(content.textContent.includes("café 👋"), "Unicode was corrupted");
        assert(!content.querySelector("script, img, iframe, svg"), "Unsafe model markup survived");
        for (const element of content.querySelectorAll("*")) {
            assert(![...element.attributes].some(attribute => /^on/i.test(attribute.name)), "Event handler survived");
            assert(!/^javascript:/i.test(element.getAttribute("href") || ""), "Unsafe URL survived");
        }
        assert(!page.__markdownXss, "Untrusted markup executed");
    };

    try {
        await openPage();
        assert(typeof page.marked.parse === "function" && page.DOMPurify.isSupported, "Markdown libraries did not load");
        assert(userContents()[0]?.textContent === "My favorite programming language is Python.", "Saved user was not loaded");
        assert(assistantContents()[0]?.querySelector("h2")?.textContent === "Saved reply", "Saved assistant Markdown was not loaded");
        assert(assistantContents()[0].querySelector("strong")?.textContent === "Python", "Saved bold text missing");

        const userText = 'Show Markdown. <img src=x onerror="window.__markdownXss=true">';
        setInput(userText);
        const sending = page.sendMessage();
        await waitFor(() => assistantContents().at(-1)?.querySelector("h1"), "progressive heading before completion");
        assert(!ready() && doc.getElementById("sendButton").disabled && doc.getElementById("newChatButton").disabled, "Generation did not disable controls");
        assert(userContents().at(-1).textContent === userText && !userContents().at(-1).children.length, "User text became HTML");
        const partial = await state();
        assert(partial.messages.length === 3 && partial.messages.at(-1).role === "user", "Partial assistant text was saved");
        assert(partial.model_calls[0].messages[0].role === "system", "System prompt was not prepended");
        assert(partial.model_calls[0].messages[1].content.includes("Python"), "Ollama did not receive database history");
        const post = requests.find(request => request.url === "/chat");
        const payload = JSON.parse(post.body);
        assert(Object.keys(payload).join() === "message" && payload.message === userText, "Frontend sent more than the newest message");
        await fetch("/__lesson9_release", { method: "POST" });
        await sending;
        assert(ready(), "Controls stayed disabled after completion");
        const completed = await state();
        assert(completed.messages.length === 4, "Expected one completed assistant row");
        assert(completed.messages.at(-1).content === completed.sample, "Complete raw Markdown was not saved");
        checkMarkdown(assistantContents().at(-1));
        const conversation = doc.getElementById("conversation");
        assert(conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 85, "Conversation did not scroll to the reply");

        await openPage();
        assert(userContents().length === 2 && assistantContents().length === 2, "Refresh lost saved messages");
        checkMarkdown(assistantContents().at(-1));
        setInput("What programming language did I say I like?");
        await page.sendMessage();
        const followup = await state();
        assert(followup.model_calls.at(-1).messages[1].content.includes("Python"), "Follow-up after refresh lost early history");
        assert(followup.model_calls.at(-1).messages.length === 6, "Follow-up did not receive all database history");
        assert(assistantContents().at(-1).querySelector("strong")?.textContent === "Python", "Follow-up response missing");

        if (mobile) {
            doc.getElementById("menuButton").click();
            assert(doc.body.classList.contains("sidebar-open"), "Mobile sidebar did not open");
        }
        doc.getElementById("newChatButton").click();
        await waitFor(ready, "New chat deletion");
        assert((await state()).messages.length === 0, "New chat did not delete database rows");
        assert(!doc.getElementById("chat").children.length && !doc.getElementById("welcome").hidden, "New chat did not clear the UI");
        assert(doc.activeElement === doc.getElementById("messageInput"), "New chat did not focus the textarea");
        assert(!doc.body.classList.contains("sidebar-open"), "New chat did not close the mobile sidebar");

        setInput("Fresh first line\nFresh second line");
        const shiftEnter = new page.KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true, cancelable: true });
        const postsBefore = requests.filter(request => request.url === "/chat").length;
        doc.getElementById("messageInput").dispatchEvent(shiftEnter);
        assert(!shiftEnter.defaultPrevented, "Shift+Enter was prevented from creating a newline");
        assert(requests.filter(request => request.url === "/chat").length === postsBefore, "Shift+Enter sent a request");
        const enter = new page.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
        doc.getElementById("messageInput").dispatchEvent(enter);
        assert(enter.defaultPrevented, "Enter was not handled as send");
        await waitFor(() => ready() && assistantContents().at(-1)?.querySelector("h1"), "Enter response");
        const fresh = await state();
        assert(fresh.messages.length === 2 && fresh.model_calls.at(-1).messages.length === 2, "New conversation reused old history");
        assert(fresh.messages[0].content.includes("\n"), "Textarea lost the multiline message");
        assert(doc.documentElement.scrollWidth <= page.innerWidth + 1, "Layout overflows the viewport");

        // Simulate transient HTTP failures while preserving the real database.
        // Recovery must reload SQLite rather than guessing which writes happened.
        const realFetch = page.fetch;
        const failNext = (urlToFail, methodToFail) => {
            let pending = true;
            page.fetch = (url, options = {}) => {
                if (pending && url === urlToFail && (options.method || "GET") === methodToFail) {
                    pending = false;
                    return Promise.resolve(new page.Response(JSON.stringify({ detail: "Test service unavailable" }), {
                        status: 503, headers: { "Content-Type": "application/json" }
                    }));
                }
                return realFetch(url, options);
            };
        };
        failNext("/messages", "GET");
        await page.initializeConversation();
        assert(ready(), "History load failure left controls disabled");
        assert(!doc.getElementById("storageNotice").hidden && doc.getElementById("storageNotice").textContent.includes("503"), "History load failure did not show a notice");
        assert(userContents().length === 1 && assistantContents().length === 1, "History load failure erased the current view");

        failNext("/messages", "DELETE");
        doc.getElementById("newChatButton").click();
        await waitFor(ready, "failed New chat recovery");
        assert((await state()).messages.length === 2, "Failed deletion changed SQLite");
        assert(userContents().length === 1 && assistantContents().length === 1, "Failed deletion lost the saved conversation");
        assert(doc.querySelector(".message-error")?.textContent.includes("503"), "Failed deletion did not show an error");

        failNext("/chat", "POST");
        setInput("This failed request must not become saved history");
        await page.sendMessage();
        assert(ready() && (await state()).messages.length === 2, "Failed send changed SQLite or left controls disabled");
        assert(userContents().length === 1 && assistantContents().length === 1, "Failed send left an unsaved turn in the restored view");
        assert(doc.querySelector(".message-error")?.textContent.includes("503"), "Failed send did not show an error");
        page.fetch = realFetch;

        await report({
            result: "PASS", viewport: [page.innerWidth, page.innerHeight],
            checks: "SQLite history load, newest-message API, progressive Markdown, one completed row, actual reload, context after reload, New chat DB/UI/focus, Enter/Shift+Enter, sanitized HTML, responsive layout, failed load/delete/send recovery"
        });
    } catch (error) {
        // Release a gated stream even when an earlier assertion fails.
        await fetch("/__lesson9_release", { method: "POST" }).catch(() => {});
        await report({ result: "FAIL", error: error.stack });
    }
})();
