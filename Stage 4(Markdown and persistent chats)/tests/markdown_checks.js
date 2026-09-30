// Browser regression checks: use the real page, Marked, and DOMPurify.
// Only the model response is mocked, so these checks do not require Ollama.
(async () => {
    const assert = (condition, message) => {
        if (!condition) throw new Error(message);
    };
    const tick = () => new Promise(resolve => setTimeout(resolve, 5));
    const fence = String.fromCharCode(96).repeat(3);
    const prefix = "# Markdown demo\n\n**Bold text** and *emphasis*.\n\n";
    const sample = prefix + [
        "## Lists", "", "- First item", "- Second item", "",
        "1. First step", "2. Second step", "",
        "## Comparison", "", "| Feature | Status |", "| --- | --- |",
        "| Streaming | Works |", "| Markdown | Works |", "",
        "## Code", "", "Use `print()` to display text.", "",
        fence + "python", 'print("Hello, XYZ!")',
        'html = "<script>alert(1)</script>"', fence, "",
        "> A useful note.", "", "[Documentation](https://example.com)", "",
        "Unicode: caf\u00e9 \ud83d\udc4b", ""
    ].join("\n");
    const requests = [];
    let nextResponse;
    window.fetch = async (url, options) => {
        assert(url === "/chat", "Unexpected request URL");
        requests.push(JSON.parse(options.body));
        return nextResponse;
    };
    try {
        assert(typeof marked.parse === "function", "Marked did not load");
        assert(DOMPurify.isSupported, "DOMPurify did not load");
        assert(getComputedStyle(document.querySelector(".app-shell")).display === "grid", "Stylesheet did not load");
        let controller;
        nextResponse = new Response(new ReadableStream({ start(c) { controller = c; } }));
        const userText = "Show **Markdown**. <img src=x onerror=alert(1)>";
        input.value = userText;
        const sending = sendMessage();
        await tick();
        assert(button.disabled && input.disabled, "Controls were not disabled");
        assert(chat.querySelector(".message-user .message-content").textContent === userText, "User message changed");
        assert(!chat.querySelector(".message-user img, .message-user strong"), "User text became HTML");
        const output = chat.querySelector(".message-assistant .message-content");
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode(prefix));
        await tick();
        assert(output.querySelector("h1")?.textContent === "Markdown demo", "Heading was not rendered during streaming");
        assert(output.querySelector("strong")?.textContent === "Bold text", "Bold text was not rendered during streaming");
        assert(isSending && messages.length === 1, "Partial response was saved as complete");

        // Seven-byte chunks split Markdown syntax and multibyte characters.
        const remaining = encoder.encode(sample.slice(prefix.length));
        for (let offset = 0; offset < remaining.length; offset += 7) {
            controller.enqueue(remaining.slice(offset, offset + 7));
            await tick();
            assert(messages.length === 1, "Incomplete reply entered history");
            assert(!output.querySelector("script, img, iframe, svg"), "Unsafe node appeared during streaming");
        }
        controller.close();
        await sending;
        assert(output.querySelectorAll("h2").length === 3, "Headings missing");
        assert(output.querySelectorAll("ul > li").length === 2, "Unordered list missing");
        assert(output.querySelectorAll("ol > li").length === 2, "Ordered list missing");
        assert(output.querySelectorAll("table tbody tr").length === 2, "Table missing");
        assert(output.querySelector("table th")?.textContent === "Feature", "Table header missing");
        assert(output.querySelector("pre code")?.textContent.includes('print("Hello, XYZ!")'), "Code block missing");
        assert(output.querySelector("pre code").textContent.includes("<script>alert(1)</script>"), "Code was interpreted as markup");
        assert(output.querySelector("p code")?.textContent === "print()", "Inline code missing");
        assert(output.querySelector("blockquote") && output.querySelector("a"), "Quote or link missing");
        assert(output.textContent.includes("caf\u00e9 \ud83d\udc4b"), "Split Unicode was corrupted");
        assert(messages[1].content === sample, "History stored HTML instead of original Markdown");
        assert(getComputedStyle(output).whiteSpace === "normal", "Markdown inherited pre-wrap");
        assert(getComputedStyle(output.querySelector("pre")).overflowX === "auto", "Code cannot scroll horizontally");

        nextResponse = new Response("**Follow-up** answer.");
        input.value = "Remember the example?";
        await sendMessage();
        assert(requests[1].messages[1].content === sample, "Follow-up did not receive original Markdown");

        // Test each adversarial prefix as it might arrive over the wire.
        const attacks = [
            '<img src=x onerror="window.__markdownXss = true">',
            '<scr' + 'ipt>window.__markdownXss = true</scr' + 'ipt>',
            '[Click](javascript:alert(1))',
            '<a href="jav&#x61;script:alert(1)" onclick="window.__markdownXss=true">link</a>',
            '<svg onload="window.__markdownXss=true"></svg>',
            '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
            '<p style="position:fixed;inset:0" class="app-shell" id="messageInput">text</p>',
            '<form><input name="messageInput"></form>'
        ];
        const safetyOutput = addMessage("AI", "");
        window.__markdownXss = false;
        for (const attack of attacks) {
            for (let length = 1; length <= attack.length; length += 3) {
                renderAssistantMessage(safetyOutput, attack.slice(0, length));
                assert(!safetyOutput.querySelector("script, img, iframe, svg, style, form, input"), "Unsafe element survived sanitizing");
                for (const element of safetyOutput.querySelectorAll("*")) {
                    for (const attribute of element.attributes) {
                        assert(!/^on/i.test(attribute.name) && !["style", "id", "name", "class"].includes(attribute.name), "Unsafe attribute survived");
                    }
                    if (element.hasAttribute("href")) {
                        assert(!/^javascript:/i.test(element.getAttribute("href").replace(/\s/g, "")), "Unsafe URL survived");
                    }
                }
            }
            renderAssistantMessage(safetyOutput, attack);
        }
        await tick();
        assert(!window.__markdownXss, "Script execution occurred");

        const purifier = window.DOMPurify;
        window.DOMPurify = undefined;
        renderAssistantMessage(safetyOutput, "**Literal fallback** <img src=x>");
        assert(!safetyOutput.children.length && safetyOutput.textContent.includes("<img"), "Missing sanitizer did not fall back to plain text");
        window.DOMPurify = purifier;

        const beforeError = messages.length;
        let brokenController;
        nextResponse = new Response(new ReadableStream({start(c) { brokenController = c; }}));
        input.value = "Retry this answer";
        const interrupted = sendMessage();
        await tick();
        brokenController.enqueue(encoder.encode("**Partial** reply"));
        await tick();
        brokenController.error(new Error("Connection interrupted"));
        await interrupted;
        const assistantReplies = chat.querySelectorAll(".message-assistant .message-content");
        assert(assistantReplies[assistantReplies.length - 1].querySelector("strong"), "Partial Markdown was lost after an error");
        assert(messages.length === beforeError && input.value === "Retry this answer" && !button.disabled, "Failed request did not recover");

        // Leave a useful preview for the desktop/mobile screenshot.
        newChatButton.click();
        addMessage("You", "Show me a Markdown example.");
        const preview = addMessage("AI", "");
        renderAssistantMessage(preview, sample);
        const longOutput = addMessage("AI", "");
        renderAssistantMessage(longOutput, fence + "\n" + "long_code_".repeat(100) + "\n" + fence + "\n\n|" + " column |".repeat(12) + "\n|" + " --- |".repeat(12) + "\n|" + " value |".repeat(12));
        assert(document.documentElement.scrollWidth <= innerWidth + 1, "Markdown overflows the page");
        longOutput.closest(".message").remove();
        conversation.scrollTop = 0;
        const result = { result: "PASS", viewport: [innerWidth, innerHeight], checks: "streaming headings/bold/lists/tables/code, split Unicode, raw history, sanitization, fallback, interrupted stream, responsive overflow" };
        document.body.dataset.result = JSON.stringify(result);
        parent.postMessage(result, location.origin);
    } catch (error) {
        const result = { result: "FAIL", error: error.stack };
        document.body.dataset.result = JSON.stringify(result);
        parent.postMessage(result, location.origin);
    }
})();
