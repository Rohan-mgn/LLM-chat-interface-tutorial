(async () => {
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const frame = document.getElementById("appFrame");
    frame.style.width = new URLSearchParams(location.search).has("mobile") ? "390px" : "100%";
    let checks = 0;
    const report = result => fetch("/__stage9_result", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(result) });
    try {
        const loaded = new Promise(resolve => frame.addEventListener("load", resolve, { once: true }));
        frame.src = "/?composer-buttons";
        await loaded;
        const page = frame.contentWindow, doc = frame.contentDocument;
        const input = doc.getElementById("messageInput");
        for (let i = 0; doc.getElementById("newChatButton").disabled && i < 500; i++) await sleep(20);
        assert(!doc.getElementById("newChatButton").disabled, "App did not initialize");
        const set = (text, start = text.length, end = start) => {
            input.value = text;
            input.focus();
            input.setSelectionRange(start, end);
            input.dispatchEvent(new page.Event("input", { bubbles: true }));
        };
        const click = id => {
            const button = doc.getElementById(id);
            assert(!button.disabled, id + " unexpectedly disabled");
            button.focus(); // Match moving focus away from the textarea to a real button.
            button.click();
        };
        const expect = text => { assert(input.value === text, "Unexpected text: " + JSON.stringify(input.value)); checks++; };
        set("- First item");
        click("indentButton"); expect("  - First item");
        assert(doc.activeElement === input, "Indent did not restore textarea focus");
        assert(doc.execCommand("undo"), "Native undo unavailable");
        expect("- First item");
        assert(doc.execCommand("redo"), "Native redo unavailable");
        expect("  - First item");
        click("unindentButton"); expect("- First item");
        click("unindentButton"); expect("- First item"); // No spaces: safe no-op.

        const fence = String.fromCharCode(96).repeat(3);
        const code = fence + "python\nprint('Hello')\n" + fence;
        set(code, code.indexOf("print") + 5);
        click("indentButton"); expect(fence + "python\n  print('Hello')\n" + fence);
        click("unindentButton"); expect(code);
        set(fence + "python\n    print(1)", (fence + "python\n    print(1)").length);
        click("unindentButton"); expect(fence + "python\n  print(1)");
        set(fence + "python\n\tprint(1)");
        click("unindentButton"); expect(fence + "python\nprint(1)");

        set("- One\n- Two\n- Three", 0, 11);
        click("indentButton"); expect("  - One\n  - Two\n- Three");
        assert(input.selectionStart === 2 && input.selectionEnd === 15, "Multiline selection not retained");
        click("unindentButton"); expect("- One\n- Two\n- Three");
        set("one\ntwo\nthree", 0, 8); // Ends exactly at the start of the third line.
        click("indentButton"); expect("  one\n  two\nthree");
        click("unindentButton"); expect("one\ntwo\nthree");
        set("Ordinary text");
        click("indentButton"); expect("Ordinary text");
        click("unindentButton"); expect("Ordinary text");

        set("- Disabled while sending");
        page.setBusy("sending");
        assert(doc.getElementById("indentButton").disabled && doc.getElementById("unindentButton").disabled, "Busy buttons not disabled");
        doc.getElementById("indentButton").click();
        expect("- Disabled while sending");
        page.setBusy();
        set("");
        await report({ result: "PASS", viewport: [page.innerWidth, page.innerHeight], checks: checks + " button outcomes; list/code/multiline indentation; tabs; selection boundaries; focus; undo/redo; prose no-op; disabled during generation" });
    } catch (error) {
        await report({ result: "FAIL", error: error.stack });
    }
})();