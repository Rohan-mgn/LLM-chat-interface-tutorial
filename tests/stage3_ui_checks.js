(async () => {
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 15));
    const requests = [];
    let nextResponse, networkError = false;
    window.fetch = async (url, options) => {
        assert(url === "/chat", "Unexpected URL");
        requests.push(JSON.parse(options.body));
        if (networkError) throw new Error("Network unavailable");
        return nextResponse;
    };
    const draft = (text) => { input.value = text; input.dispatchEvent(new Event("input")); };
    const finish = async () => {
        for (let i = 0; isSending && i < 150; i++) await tick();
        assert(!isSending, "Request did not finish");
    };
    try {
        await tick();
        assert(input.tagName === "TEXTAREA" && button.disabled, "Textarea or empty-send guard missing");
        assert(document.documentElement.scrollWidth <= innerWidth + 1, "Page overflows horizontally");
        const composerBox = document.getElementById("messageForm").getBoundingClientRect();
        assert(composerBox.bottom <= innerHeight && composerBox.left >= 0, "Composer outside viewport");

        if (mobileLayout.matches) {
            assert(sidebar.inert && !mainContent.inert, "Closed mobile sidebar takes focus");
            menuButton.click();
            assert(!sidebar.inert && mainContent.inert && menuButton.getAttribute("aria-expanded") === "true", "Mobile sidebar did not open");
            document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape", bubbles:true}));
            assert(sidebar.inert && !mainContent.inert, "Escape did not close sidebar");
            menuButton.click();
            document.getElementById("sidebarBackdrop").click();
            assert(!document.body.classList.contains("sidebar-open"), "Backdrop did not close sidebar");
        } else {
            assert(!sidebar.inert && sidebar.getBoundingClientRect().right <= mainContent.getBoundingClientRect().left + 1, "Desktop sidebar layout incorrect");
        }

        document.querySelector(".suggestion").click();
        assert(input.value.length > 10 && !button.disabled, "Suggestion did not fill composer");
        draft("line\n".repeat(20));
        assert(input.getBoundingClientRect().height <= 180 && input.getBoundingClientRect().height > 28, "Textarea resize incorrect");
        const shift = new KeyboardEvent("keydown", {key:"Enter", shiftKey:true, bubbles:true, cancelable:true});
        input.dispatchEvent(shift);
        assert(!shift.defaultPrevented && requests.length === 0, "Shift+Enter was intercepted");
        const composing = new KeyboardEvent("keydown", {key:"Enter", isComposing:true, bubbles:true, cancelable:true});
        input.dispatchEvent(composing);
        assert(!composing.defaultPrevented && requests.length === 0, "IME composition was sent");

        const userText = "Hello my name is XYZ.\nWhat are you? <img src=x onerror=alert(1)>";
        draft(userText);
        let controller;
        nextResponse = new Response(new ReadableStream({start(c) {controller = c;}}));
        const pending = sendMessage();
        await tick();
        assert(button.disabled && input.disabled && newChatButton.disabled, "Generation controls not disabled");
        assert(welcome.hidden && !chat.hidden && chat.textContent.includes("Waiting for a reply"), "Waiting state missing");
        assert(!chat.querySelector("img") && chat.querySelector(".message-user .message-content").textContent === userText, "User text was interpreted as HTML");
        draft("Duplicate");
        await sendMessage();
        assert(requests.length === 1, "Concurrent request sent");
        const encoder = new TextEncoder();
        const prefix = "Hello XYZ! Here are some thoughts.\n" + "A line of a longer answer.\n".repeat(50);
        controller.enqueue(encoder.encode(prefix));
        await tick();
        assert(chat.querySelector(".message-assistant .message-content").textContent === prefix, "Streaming text not displayed before completion");
        assert(messages.length === 1, "Partial reply saved to history");
        assert(conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 3, "New output did not automatically scroll");
        conversation.scrollTop = 0;
        conversation.dispatchEvent(new Event("scroll"));
        const suffix = "I\u2019m an AI \u2014 <b>assistant</b> \ud83d\udc4b.";
        const bytes = encoder.encode(suffix);
        controller.enqueue(bytes.slice(0, 2));
        await tick();
        controller.enqueue(bytes.slice(2, bytes.length - 3));
        await tick();
        controller.enqueue(bytes.slice(bytes.length - 3));
        await tick();
        assert(conversation.scrollTop === 0, "Streaming forced a reader away from earlier messages");
        controller.close();
        await pending;
        assert(messages[1].content === prefix + suffix && !chat.querySelector("b"), "UTF-8 decoding or safe assistant text failed");
        assert(!input.disabled && !newChatButton.disabled, "Controls did not recover");

        draft("What is my name?\nPlease keep it short.");
        nextResponse = new Response("Your name is XYZ.");
        const enter = new KeyboardEvent("keydown", {key:"Enter", bubbles:true, cancelable:true});
        input.dispatchEvent(enter);
        assert(enter.defaultPrevented, "Enter did not send");
        await finish();
        assert(requests[1].messages.length === 3 && requests[1].messages[2].content.includes("\n"), "History or multiline message lost");
        assert(messages.length === 4, "Completed reply missing");
        const savedLength = messages.length;

        for (const item of [
            [new Response("Unavailable", {status:503}), "HTTP 503"],
            [new Response(""), "empty reply"],
            [new Response(null), "response stream"],
            [new Response(new ReadableStream({start(c) {c.error(new Error("Stream interrupted"));}})), "Stream interrupted"]
        ]) {
            nextResponse = item[0];
            draft("Please retry this");
            await sendMessage();
            assert(chat.textContent.includes(item[1]), "Error was not shown: " + item[1]);
            assert(messages.length === savedLength && input.value === "Please retry this" && !input.disabled && !button.disabled, "Failed turn was not restored");
        }
        networkError = true;
        await sendMessage();
        assert(chat.textContent.includes("Network unavailable") && messages.length === savedLength, "Network failure handling missing");
        networkError = false;
        newChatButton.click();
        assert(messages.length === 0 && chat.children.length === 0 && !welcome.hidden && chat.hidden && button.disabled, "New chat did not reset");
        assert(document.documentElement.scrollWidth <= innerWidth + 1, "Layout overflow after conversation");

        if (location.search.includes("conversation")) {
            addMessage("You", "Hello, my name is XYZ. What are you?");
            addMessage("AI", "Hi XYZ! I'm your AI assistant.\n\nI can help you understand a topic, explore an idea, or find the right words. I can also use what we've discussed earlier in this conversation.\n\nWhat would you like to work on?");
            conversationTitle.textContent = "Hello, my name is XYZ. What are you?";
            scrollToBottom(true);
        }
        const details = {result:"PASS", viewport:[innerWidth, innerHeight], checks:"layout, mobile drawer, textarea, Enter/Shift+Enter/IME, streaming, Unicode, scrolling, history, safe DOM, request guards, HTTP/empty/network/stream errors, New chat"};
        document.body.dataset.result = JSON.stringify(details);
        parent.postMessage(details, "*");
    } catch (error) {
        const details = {result:"FAIL", viewport:[innerWidth, innerHeight], error:error.stack};
        document.body.dataset.result = JSON.stringify(details);
        parent.postMessage(details, "*");
    }
})();
