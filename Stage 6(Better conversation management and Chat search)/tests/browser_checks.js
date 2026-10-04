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
        failNext(`/conversations/${b}/messages?include_ids=true`, "GET");
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
        const c = activeId();
        const showSidebar = () => { if (mobile) doc.getElementById("menuButton").click(); };
        const typeSearch = value => {
            showSidebar();
            const search = doc.getElementById("chatSearch");
            search.value = value;
            search.dispatchEvent(new page.Event("input", { bubbles: true }));
        };
        const countSearch = () => requests.filter(request => request.url.startsWith("/search?")).length;
        const startSearchCount = countSearch();
        typeSearch("p"); typeSearch("pi"); typeSearch("pineapple");
        await waitFor(() => doc.querySelectorAll(".search-hit").length === 1, "debounced search");
        assert(countSearch() === startSearchCount + 1, "Search was not debounced");
        assert(activeId() === c, "Typing search changed the selected chat");
        assert(doc.querySelector(".search-hit mark")?.textContent.toLowerCase() === "pineapple", "Search did not highlight safely");
        assert(doc.querySelector(".search-source").textContent.includes("message #"), "Matching message not identified");
        assert(doc.getElementById("conversationList").hidden, "Search results not distinct from sidebar");
        doc.querySelector(".search-hit").focus();
        assert(doc.activeElement === doc.querySelector(".search-hit"), "Result is not keyboard focusable");
        doc.querySelector(".search-hit").click();
        await waitFor(() => ready() && activeId() === a, "search result opens A");
        assert(users().length === 4, "Search opened incorrect history");
        assert(doc.activeElement.matches(".message.search-target") &&
            doc.activeElement.textContent.includes("pineapple"), "Search did not focus its matching message");
        typeSearch("<img");
        await waitFor(() => doc.querySelectorAll(".search-hit").length === 2, "literal HTML search");
        assert(!doc.getElementById("searchResults").querySelector("img, script"), "Search injected HTML");
        typeSearch("no-result-unique-zz");
        await waitFor(() => doc.getElementById("searchStatus").textContent.includes("No chats"), "zero search results");
        const beforeClear = countSearch();
        doc.getElementById("clearSearch").click();
        assert(countSearch() === beforeClear && !doc.getElementById("conversationList").hidden && activeId() === a, "Clear search sent request or lost selection");

        // A late older response must never replace the latest query's results.
        const searchFetch = page.fetch;
        let resolveOld;
        page.fetch = (url, options) => url === "/search?q=late-old"
            ? new Promise(resolve => { resolveOld = resolve; }) : searchFetch(url, options);
        typeSearch("late-old");
        await waitFor(() => resolveOld, "old search in flight");
        typeSearch("pineapple");
        await waitFor(() => doc.querySelector(".search-hit mark")?.textContent.toLowerCase() === "pineapple", "latest search response");
        resolveOld(new page.Response(JSON.stringify({ results: [], has_more: false }), { status: 200 }));
        await tick(); await tick();
        assert(doc.querySelectorAll(".search-hit").length === 1, "Late search overwrote current results");
        page.fetch = searchFetch;
        doc.getElementById("clearSearch").click();

        const now = new Date(2026, 9, 20, 12);
        const ages = [0, 1, 4, 14, 40];
        const groups = ["Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older"];
        ages.forEach((age, i) => {
            const date = new Date(2026, 9, 20 - age, 12).toISOString().replace("T", " ").slice(0, -1);
            assert(page.dateGroup(date, now) === groups[i], "Wrong date group " + groups[i]);
        });
        assert(doc.querySelector(".date-group").textContent === "Today", "Sidebar date heading absent");
        await page.refreshSidebar(); await page.refreshSidebar();
        assert(doc.querySelectorAll(".conversation-item").length === 3 && activeId() === a, "Sidebar refresh duplicated or lost selection");

        const sidebarFetch = page.fetch;
        let resolveSidebar;
        page.fetch = (url, options) => url === "/conversations"
            ? new Promise(resolve => { resolveSidebar = resolve; }) : sidebarFetch(url, options);
        const staleSidebar = page.refreshSidebar();
        page.fetch = sidebarFetch;
        await page.refreshSidebar();
        resolveSidebar(new page.Response(JSON.stringify({ conversations: [] }), { status: 200 }));
        await staleSidebar;
        assert(doc.querySelectorAll(".conversation-item").length === 3 && activeId() === a,
            "Late sidebar refresh overwrote newer state");

        const action = (id, label) => {
            showSidebar();
            const row = doc.querySelector(`#conversationList [data-conversation-id="${id}"]`).closest(".conversation-row");
            row.querySelector("summary").click();
            [...row.querySelectorAll(".conversation-actions button")].find(button => button.textContent === label).click();
        };
        action(a, "Rename");
        assert(doc.getElementById("conversationDialog").open, "Rename dialog missing");
        const manualTitle = '<img src=x onerror=alert(1)> Pineapple notes';
        doc.getElementById("renameInput").value = manualTitle;
        doc.getElementById("conversationActionForm").requestSubmit();
        await waitFor(() => ready() && !doc.getElementById("conversationDialog").open, "rename saved");
        assert(doc.getElementById("conversationTitle").textContent === manualTitle, "Renamed active title not updated");
        const renamed = doc.querySelector(`#conversationList [data-conversation-id="${a}"]`);
        assert(renamed.title === manualTitle && !renamed.children.length, "Full title inaccessible or unsafe");
        assert(page.getComputedStyle(renamed).textOverflow === "ellipsis", "Title ellipsis missing");
        assert((await state()).conversations.find(item => item.id === a).title === manualTitle, "Rename not persisted");
        action(a, "Delete");
        assert(doc.getElementById("dialogDescription").textContent.includes("cannot be undone"), "Deletion not confirmed");
        doc.getElementById("cancelAction").click();
        assert((await state()).conversations.length === 3 && activeId() === a, "Cancel deleted history");

        failNext(`/conversations/${a}`, "DELETE");
        action(a, "Delete");
        doc.getElementById("conversationActionForm").requestSubmit();
        await waitFor(() => ready() && doc.getElementById("dialogError").textContent, "delete error recovery");
        assert(doc.getElementById("conversationDialog").open && activeId() === a, "Failed deletion changed active chat");
        doc.getElementById("cancelAction").click();
        page.fetch = searchFetch;

        action(a, "Delete");
        doc.getElementById("conversationActionForm").requestSubmit();
        await waitFor(() => ready() && activeId() !== a && !doc.getElementById("conversationDialog").open, "active deletion selects another chat");
        assert(!(await state()).messages[a] && activeId(), "Cascade deletion or fallback selection failed");
        assert(doc.querySelectorAll(".conversation-item").length === 2, "Deleted sidebar entry remains");
        const remainingSelected = activeId();
        const other = remainingSelected === b ? c : b;
        action(other, "Delete");
        doc.getElementById("conversationActionForm").requestSubmit();
        await waitFor(() => ready() && doc.querySelectorAll(".conversation-item").length === 1, "non-active deletion");
        assert(activeId() === remainingSelected, "Deleting another chat changed selection");

        newChat();
        await send("Title test: help me plan a garden");
        const automaticId = activeId();
        await waitFor(() => doc.querySelector('#conversationList [aria-current="page"]')?.textContent === "Planning a Garden", "automatic title refresh");
        assert((await state()).conversations.find(item => item.id === automaticId).title_source === "auto", "Automatic title not saved");
        await openPage();
        assert(activeId() === automaticId && doc.getElementById("conversationTitle").textContent === "Planning a Garden", "Automatic title or selection lost on refresh");
        newChat();
        await send("ß".repeat(300) + " 😀 Straße <img>");
        typeSearch("STRASSE");
        await waitFor(() => doc.querySelector(".search-hit mark"), "Unicode search highlight");
        assert(doc.querySelector(".search-hit mark").textContent === "Straße", "Unicode highlight does not match original characters");
        assert(doc.querySelector(".search-hit").textContent.includes("😀 Straße <img>"), "Unicode snippet lost matching context");
        assert(!doc.querySelector(".search-hit img"), "Unicode result interpreted HTML");
        newChat();
        assert(!doc.querySelector('#searchResults [aria-current="page"]'), "New chat retained a stale search selection");
        doc.getElementById("clearSearch").click();
        for (const item of (await state()).conversations) {
            action(item.id, "Delete");
            doc.getElementById("conversationActionForm").requestSubmit();
            await waitFor(() => ready() && !doc.getElementById("conversationDialog").open, "delete remaining chat");
        }
        assert(activeId() === null && !doc.getElementById("welcome").hidden && doc.querySelector(".conversation-list-empty"), "Last deletion did not restore empty state");
        assert((await state()).conversations.length === 0 && doc.activeElement === doc.getElementById("messageInput"), "Empty state database or focus incorrect");
        await report({ result: "PASS", viewport: [page.innerWidth, page.innerHeight], checks: "Lesson 10 regression; title fallback/automatic refresh; rename; confirmed/cancelled/failed active and inactive deletion; cascade; empty state; date groups; debounce; literal safe highlights; snippets; late-response protection; keyboard and mobile UI" });
    } catch (error) {
        await fetch("/__lesson10_release", { method: "POST" }).catch(() => {});
        await report({ result: "FAIL", error: error.stack });
    }
})();
