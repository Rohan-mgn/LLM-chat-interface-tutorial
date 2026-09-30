(async () => {
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const stage = Number(document.body.dataset.stage);
    const requests = [];
    window.fetch = async (url, options) => {
        assert(url === "/chat" && options.method === "POST", "Chat request was not sent correctly");
        requests.push(JSON.parse(options.body));
        return new Response(JSON.stringify({reply: requests.length === 1 ? "Hello XYZ!" : "Your name is XYZ."}), {headers: {"Content-Type": "application/json"}});
    };
    try {
        input.value = "Hello my name is XYZ";
        await sendMessage();
        assert(chat.textContent.includes("Hello XYZ!"), "Assistant reply was not displayed");
        input.value = "What is my name?";
        await sendMessage();
        assert(chat.textContent.includes("Your name is XYZ."), "Follow-up reply was not displayed");
        if (stage === 1) {
            assert(requests[0].message === "Hello my name is XYZ" && requests[1].message === "What is my name?", "Stage 1 single-message request incorrect");
        } else {
            assert(requests[0].messages.length === 1 && requests[1].messages.length === 3, "Conversation history was not sent");
            assert(requests[1].messages[1].role === "assistant" && requests[1].messages[1].content === "Hello XYZ!", "Assistant turn missing from history");
            assert(messages.length === 4, "User and assistant turns were not saved");
        }
        document.body.dataset.result = JSON.stringify({result: "PASS", stage, checks: "send, display, request shape, and conversation history where applicable"});
    } catch (error) {
        document.body.dataset.result = JSON.stringify({result: "FAIL", error: error.stack});
    }
})();
