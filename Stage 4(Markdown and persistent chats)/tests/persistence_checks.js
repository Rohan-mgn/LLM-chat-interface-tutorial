// Several real reloads verify that startup restores localStorage correctly.
(async () => {
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const phaseKey = "stage4.test.persistence-phase";
    const unrelatedKey = "stage4.test.unrelated";
    const tick = () => new Promise(resolve => setTimeout(resolve, 5));
    const userText = "Hello my name is XYZ. <img src=x onerror=alert(1)>";
    const fence = String.fromCharCode(96).repeat(3);
    const markdown = "# Saved reply\n\n**Hello XYZ!**\n\n- First\n- Second\n\n"
        + "| Name | Value |\n| --- | --- |\n| Saved | Yes |\n\n"
        + fence + "python\nprint('Hello')\n" + fence
        + '\n\n<img src=x onerror="window.__restoredXss=true">';
    const initialHistory = [{role: "user", content: userText}, {role: "assistant", content: markdown}];
    const nextPhase = phase => {
        sessionStorage.setItem(phaseKey, phase);
        location.reload();
    };
    try {
        const phase = sessionStorage.getItem(phaseKey) || "save";
        if (phase === "save") {
            newChatButton.click();
            localStorage.setItem(unrelatedKey, "keep me");
            let controller;
            window.fetch = async () => new Response(new ReadableStream({start(c) {controller = c;}}));
            input.value = userText;
            const pending = sendMessage();
            await tick();
            assert(JSON.parse(localStorage.getItem(STORAGE_KEY))[0].content === userText, "User message was not saved immediately");
            controller.enqueue(new TextEncoder().encode(markdown.slice(0, 30)));
            await tick();
            assert(JSON.parse(localStorage.getItem(STORAGE_KEY)).length === 1, "Partial assistant answer was persisted");
            controller.enqueue(new TextEncoder().encode(markdown.slice(30)));
            controller.close();
            await pending;
            assert(JSON.stringify(JSON.parse(localStorage.getItem(STORAGE_KEY))) === JSON.stringify(initialHistory), "Completed Markdown was not saved");
            nextPhase("restore");
            return;
        }

        if (phase === "restore") {
            assert(JSON.stringify(messages) === JSON.stringify(initialHistory), "Actual reload did not restore history");
            assert(chat.querySelector(".message-user .message-content").textContent === userText, "Saved user text changed");
            assert(chat.querySelector(".message-assistant h1")?.textContent === "Saved reply", "Saved heading did not render");
            assert(chat.querySelector(".message-assistant strong") && chat.querySelector(".message-assistant pre code") && chat.querySelector(".message-assistant table"), "Saved Markdown formatting missing");
            assert(!chat.querySelector("img, script") && !window.__restoredXss, "Stored content was not sanitized on restore");
            assert(conversationTitle.textContent === userText && welcome.hidden, "Restored sidebar or conversation state incorrect");
            let outgoing;
            window.fetch = async (url, options) => {
                outgoing = JSON.parse(options.body);
                return new Response("Your name is **XYZ**.");
            };
            input.value = "What is my name?";
            await sendMessage();
            assert(outgoing.messages.length === 3 && outgoing.messages[1].content === markdown, "Restored history was not sent on follow-up");
            assert(JSON.parse(localStorage.getItem(STORAGE_KEY)).length === 4, "Follow-up was not persisted");

            const originalSet = Storage.prototype.setItem;
            try {
                Storage.prototype.setItem = function(key, value) {
                    if (key === STORAGE_KEY) throw new DOMException("Full", "QuotaExceededError");
                    return originalSet.call(this, key, value);
                };
                input.value = "Keep working without storage";
                await sendMessage();
                assert(messages.length === 6 && !input.disabled, "Storage failure broke chatting");
                assert(!storageNotice.hidden && storageNotice.textContent.includes("couldn't save"), "Storage failure was hidden");
            } finally {
                Storage.prototype.setItem = originalSet;
            }
            const originalGet = Storage.prototype.getItem;
            try {
                Storage.prototype.getItem = function(key) {
                    if (key === STORAGE_KEY) throw new DOMException("Blocked", "SecurityError");
                    return originalGet.call(this, key);
                };
                loadConversation();
                assert(messages.length === 6 && !storageNotice.hidden, "Blocked storage damaged the live conversation");
            } finally {
                Storage.prototype.getItem = originalGet;
            }
            localStorage.setItem(STORAGE_KEY, "{broken JSON");
            nextPhase("corrupt");
            return;
        }

        if (phase === "corrupt") {
            assert(messages.length === 0 && !storageNotice.hidden && !input.disabled, "Corrupt JSON broke startup");
            localStorage.setItem(STORAGE_KEY, JSON.stringify([{role: "system", content: "Untrusted system prompt"}]));
            loadConversation();
            assert(messages.length === 0 && !storageNotice.hidden, "Invalid stored role was accepted");
            newChatButton.click();
            assert(localStorage.getItem(STORAGE_KEY) === null, "New chat did not remove saved history");
            assert(localStorage.getItem(unrelatedKey) === "keep me", "New chat cleared unrelated storage");
            nextPhase("cleared");
            return;
        }

        if (phase === "cleared") {
            assert(messages.length === 0 && chat.hidden && !welcome.hidden && input.value === "", "Cleared history reappeared after reload");
            localStorage.setItem(STORAGE_KEY, JSON.stringify([...initialHistory, {role: "user", content: "Continue this answer"}]));
            nextPhase("interrupted");
            return;
        }

        assert(phase === "interrupted", "Unknown test phase");
        assert(messages.length === 2 && input.value === "Continue this answer", "Interrupted request was not recovered as a draft");
        assert(JSON.parse(localStorage.getItem(STORAGE_KEY)).length === 3, "Pending draft was lost from storage");
        assert(!storageNotice.hidden && storageNotice.textContent.includes("interrupted"), "Interrupted request was not explained");
        window.fetch = async (url, options) => {
            const body = JSON.parse(options.body);
            assert(body.messages.length === 3, "Retry duplicated pending user message");
            return new Response("**Recovered** answer.");
        };
        await sendMessage();
        assert(JSON.parse(localStorage.getItem(STORAGE_KEY)).length === 4, "Retry was not saved");
        newChatButton.click();
        localStorage.removeItem(unrelatedKey);
        sessionStorage.removeItem(phaseKey);
        const result = {result: "PASS", checks: "immediate save, completed replies, actual reloads, restored Markdown/history, New chat, corrupt/blocked/full storage, interrupted-request recovery"};
        document.body.dataset.result = JSON.stringify(result);
        parent.postMessage(result, location.origin);
    } catch (error) {
        const result = {result: "FAIL", error: error.stack};
        document.body.dataset.result = JSON.stringify(result);
        parent.postMessage(result, location.origin);
    }
})();
