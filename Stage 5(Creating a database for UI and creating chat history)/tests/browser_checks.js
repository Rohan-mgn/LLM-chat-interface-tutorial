// Use the real page and HTTP/SQLite APIs; only Ollama is replaced in the server.
(async () => {
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 20));
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
    let page, doc;
    let requests = [];
    const report = async result => {
        document.body.dataset.result = JSON.stringify(result);
        await fetch("/__lesson10_result", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(result) });
    };
    const state = async () => (await fetch("/__lesson10_state", { cache: "no-store" })).json();
    const users = () => [...doc.querySelectorAll(".message-user .message-content")];
    const assistants = () => [...doc.querySelectorAll(".message-assistant .message-content")];
    const ready = () => !doc.getElementById("messageInput").disabled;
    const activeId = () => Number(doc.querySelector('#conversationList [aria-current="page"]')?.dataset.conversationId) || null;
    const openPage = async () => {
        const loaded = new Promise(resolve => frame.addEventListener("load", resolve, { once: true }));
        frame.src = "/?browser-check=" + Date.now();
        await loaded;
        page = frame.contentWindow;
        doc = frame.contentDocument;
        await waitFor(ready, "initialization");
        const originalFetch = page.fetch.bind(page);
        requests = [];
        page.fetch = (url, options = {}) => {
            requests.push({ url, method: options.method || "GET", body: options.body });
            return originalFetch(url, options);
        };
    };
    const setInput = text => {
        doc.getElementById("messageInput").value = text;
        doc.getElementById("messageInput").dispatchEvent(new page.Event("input", { bubbles: true }));
    };
    const send = async text => { setInput(text); await page.sendMessage(); };
    const newChat = () => {
        if (mobile) doc.getElementById("menuButton").click();
        doc.getElementById("newChatButton").click();
    };
    const select = async id => {
        if (mobile) doc.getElementById("menuButton").click();
        doc.querySelector(`[data-conversation-id="${id}"]`).click();
        await waitFor(() => ready() && activeId() === id, "conversation switch");
    };
    const checkMarkdown = content => {
        assert(content.querySelector("h1")?.textContent === "Markdown demo", "Heading missing");
        assert(content.querySelector("strong")?.textContent === "Bold text", "Bold missing");
        assert(content.querySelectorAll("ul > li").length === 2, "Bullet list missing");
        assert(content.querySelectorAll("ol > li").length === 2, "Ordered list missing");
        assert(content.querySelectorAll("table tbody tr").length === 2, "Table missing");
        assert(content.querySelector("pre code")?.textContent.includes('print("Hello, XYZ!")'), "Code missing");
        assert(content.querySelector("pre code").textContent.includes("<script>alert(1)</script>"), "Code became HTML");
        assert(content.querySelector("p code")?.textContent === "print()", "Inline code missing");
        assert(content.querySelector("blockquote") && content.textContent.includes("café 👋"), "Quote/Unicode missing");
        assert(!content.querySelector("script, img, iframe, svg"), "Unsafe element survived");
        for (const element of content.querySelectorAll("*")) {
            assert(![...element.attributes].some(attribute => /^on/i.test(attribute.name)), "Unsafe handler survived");
            assert(!/^javascript:/i.test(element.getAttribute("href") || ""), "Unsafe URL survived");
        }
        assert(!page.__markdownXss, "Untrusted script executed");
    };
    try {
        await openPage();
        assert(typeof page.marked.parse === "function" && page.DOMPurify.isSupported, "Libraries unavailable");
        assert((await state()).conversations.length === 0, "Startup created an empty chat");
        newChat(); newChat();
        assert(requests.length === 0 && (await state()).conversations.length === 0, "New chat wrote to the server");

        await send("My secret test word is pineapple.");
        const a = activeId();
        assert(a && (await state()).conversations.length === 1, "First message did not create exactly one chat");
        assert(doc.querySelector('[aria-current="page"]').textContent === "My secret test word is pineapple.", "Initial title missing");
        await send("What is my secret test word?");
        assert(assistants().at(-1).textContent.includes("pineapple"), "A forgot its secret");
        const aBefore = (await state()).messages[a];
        const requestCount = requests.length;
        newChat(); newChat();
        assert(requests.length === requestCount, "New chat sent an HTTP request");
        assert(activeId() === null && !doc.getElementById("chat").children.length, "Draft view was not cleared");
        assert(doc.activeElement === doc.getElementById("messageInput"), "Draft textarea did not receive focus");
        assert((await state()).conversations.length === 1, "Repeated New chat created empty rows");
        await send("What is my secret test word?");
        const b = activeId();
        const bState = await state();
        assert(b !== a && bState.conversations.length === 2, "B did not get a separate ID");
        assert(!assistants().at(-1).textContent.includes("pineapple"), "A's secret leaked into B");
        assert(bState.model_calls.at(-1).messages.length === 2, "B received foreign history");
        assert(JSON.stringify(bState.messages[a]) === JSON.stringify(aBefore), "Starting B changed A");

        await select(a);
        assert(users().length === 2 && assistants().length === 2, "Switch did not restore A");
        const maliciousUser = 'Show Markdown. <img src=x onerror="window.__markdownXss=true">';
        setInput(maliciousUser);
        const sending = page.sendMessage();
        await waitFor(() => assistants().at(-1)?.querySelector("h1"), "streaming heading before generation finishes");
        assert(!ready() && doc.getElementById("newChatButton").disabled, "Generation controls not disabled");
        assert([...doc.querySelectorAll("#conversationList button")].every(button => button.disabled), "Chat switches still enabled");
        doc.querySelector(`[data-conversation-id="${b}"]`).click();
        doc.getElementById("newChatButton").click();
        await page.openConversation(b);
        assert(activeId() === a, "Selection changed during streaming");
        assert(users().at(-1).textContent === maliciousUser && !users().at(-1).children.length, "User text became HTML");
        const partial = await state();
        assert(partial.messages[a].length === 5 && partial.messages[b].length === 2, "Partial reply saved or other chat modified");
        const payload = JSON.parse(requests.filter(request => request.url === "/chat").at(-1).body);
        assert(Object.keys(payload).sort().join() === "conversation_id,message" && payload.conversation_id === a, "Wrong chat request format");
        assert(partial.model_calls.at(-1).messages[0].role === "system", "System prompt lost");
        await fetch("/__lesson10_release", { method: "POST" });
        await sending;
        const completed = await state();
        assert(completed.messages[a].length === 6 && completed.messages[a].at(-1).content === completed.sample, "Completed reply not saved as one row");
        checkMarkdown(assistants().at(-1));
        const scroll = doc.getElementById("conversation");
        assert(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 85, "No automatic scrolling");
        await select(b);
        assert(users().length === 1 && !doc.querySelector(".markdown-content h1"), "A's DOM leaked into B");
        await select(a);
        checkMarkdown(assistants().at(-1));
        await openPage();
        assert(activeId() === a && users().length === 3, "Refresh did not load most recently updated chat");
        checkMarkdown(assistants().at(-1));
        await send("What is my secret word again?");
        assert(assistants().at(-1).textContent.includes("pineapple"), "A lost history after switching/refresh");
        assert((await state()).messages[b].length === 2, "Follow-up wrote to B");

        const realFetch = page.fetch;
        const failNext = (target, method) => {
            let pending = true;
            page.fetch = (url, options = {}) => {
                if (pending && url === target && (options.method || "GET") === method) {
                    pending = false;
                    return Promise.resolve(new page.Response(JSON.stringify({ detail: "Test unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } }));
                }
                return realFetch(url, options);
            };
        };
        failNext(`/conversations/${b}/messages`, "GET");
        await page.openConversation(b);
        assert(ready() && activeId() === a && users().length === 4, "Failed switch corrupted selection");
        assert(!doc.getElementById("storageNotice").hidden, "Failed switch did not show an error");
        failNext("/chat", "POST");
        await send("This unsent turn should not be saved");
        assert(users().length === 4 && (await state()).messages[a].length === 8, "Failed send corrupted stored history");
        assert(doc.querySelector(".message-error"), "Failed send did not show an error");
        newChat();
        failNext("/conversations", "POST");
        await send("Keep this draft");
        assert(doc.getElementById("messageInput").value === "Keep this draft", "Failed creation lost the user's draft");
        assert((await state()).conversations.length === 2, "Failed creation saved an empty chat");
        page.fetch = realFetch;

        newChat();
        const titleText = 'Fresh <img src=x onerror=alert(1)>\n' + 'a'.repeat(90);
        setInput(titleText);
        const shift = new page.KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true, cancelable: true });
        doc.getElementById("messageInput").dispatchEvent(shift);
        assert(!shift.defaultPrevented && (await state()).conversations.length === 2, "Shift+Enter sent a message");
        const enter = new page.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
        doc.getElementById("messageInput").dispatchEvent(enter);
        assert(enter.defaultPrevented, "Enter was not handled");
        await waitFor(() => ready() && assistants().at(-1)?.querySelector("h1"), "Enter send");
        const fresh = await state();
        assert(fresh.conversations.length === 3 && fresh.model_calls.at(-1).messages.length === 2, "Fresh chat reused old context");
        const activeButton = doc.querySelector('#conversationList [aria-current="page"]');
        assert(activeButton.textContent === titleText.replace(/\s+/g, " ").slice(0, 60) && !activeButton.children.length, "Title is unsafe or unbounded");
        assert(doc.documentElement.scrollWidth <= page.innerWidth + 1, "Responsive layout overflow");
        await report({ result: "PASS", viewport: [page.innerWidth, page.innerHeight], checks: "lazy chat creation, no deletion, pineapple A/B/A isolation, sidebar selection, streamed/sanitized Markdown, switching lock, refresh, keyboard, scrolling, safe titles, error recovery" });
    } catch (error) {
        await fetch("/__lesson10_release", { method: "POST" }).catch(() => {});
        await report({ result: "FAIL", error: error.stack });
    }
})();
