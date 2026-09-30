const input = document.getElementById("messageInput");
const button = document.getElementById("sendButton");
const chat = document.getElementById("chat");
const conversation = document.getElementById("conversation");
const welcome = document.getElementById("welcome");
const newChatButton = document.getElementById("newChatButton");
const conversationTitle = document.getElementById("conversationTitle");
const status = document.getElementById("status");
const statusText = document.getElementById("statusText");
const sendLabel = document.getElementById("sendLabel");
const menuButton = document.getElementById("menuButton");
const sidebar = document.getElementById("sidebar");
const mainContent = document.getElementById("mainContent");
const mobileLayout = window.matchMedia("(max-width: 760px)");

const messages = [];
let isSending = false;
let followOutput = true;

// Enter sends; Shift + Enter and input-method composition keep typing.
document.getElementById("messageForm").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
});
input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        document.getElementById("messageForm").requestSubmit();
    }
});
input.addEventListener("input", resizeInput);

function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
    button.disabled = isSending || input.value.trim() === "";
}

// Follow new text unless the user scrolls up to read earlier messages.
conversation.addEventListener("scroll", () => {
    followOutput = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 80;
});
function scrollToBottom(force = false) {
    if (force || followOutput) {
        conversation.scrollTop = conversation.scrollHeight;
        followOutput = true;
    }
}

function setSending(sending) {
    isSending = sending;
    input.disabled = sending;
    newChatButton.disabled = sending;
    button.disabled = sending || input.value.trim() === "";
    sendLabel.textContent = sending ? "Generating…" : "Send";
    statusText.textContent = sending ? "Replying…" : "Ready to chat";
    status.dataset.generating = String(sending);
    chat.setAttribute("aria-busy", String(sending));
}

// Create DOM elements and assign textContent; messages never become HTML.
function addMessage(role, text) {
    welcome.hidden = true;
    chat.hidden = false;
    const article = document.createElement("article");
    const label = document.createElement("span");
    const content = document.createElement("div");
    const kind = role === "You" ? "user" : role === "AI" ? "assistant" : "error";

    article.className = "message message-" + kind;
    label.className = "message-label";
    label.textContent = role === "AI" ? "My AI Assistant" : role;
    content.className = "message-content";
    content.textContent = text;
    if (kind === "error") article.setAttribute("role", "alert");

    article.append(label, content);
    chat.appendChild(article);
    scrollToBottom();
    return content;
}

async function sendMessage() {
    const message = input.value.trim();
    if (!message || isSending) return;

    setSending(true);
    if (messages.length === 0) conversationTitle.textContent = message;
    messages.push({ role: "user", content: message });
    addMessage("You", message);
    const assistantText = addMessage("AI", "Waiting for a reply…");
    assistantText.classList.add("waiting");
    input.value = "";
    resizeInput();
    scrollToBottom(true);

    let reader;
    let reply = "";
    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ messages: messages })
        });
        if (!response.ok) throw new Error("Request failed (HTTP " + response.status + "). Please try again.");
        if (!response.body) throw new Error("Your browser did not provide a response stream.");

        reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            reply += decoder.decode(value, { stream: true });
            if (reply) {
                assistantText.classList.remove("waiting");
                assistantText.textContent = reply;
                scrollToBottom();
            }
        }

        reply += decoder.decode();
        if (!reply.trim()) throw new Error("The model returned an empty reply. Please try again.");
        assistantText.textContent = reply;
        messages.push({ role: "assistant", content: reply });
    } catch (error) {
        // Restore the failed turn as a draft, ready to retry.
        messages.pop();
        input.value = message;
        assistantText.textContent = reply || "No reply received.";
        addMessage("Error", error.message);
    } finally {
        if (reader) reader.releaseLock();
        assistantText.classList.remove("waiting");
        setSending(false);
        resizeInput();
        scrollToBottom();
        input.focus({ preventScroll: true });
    }
}

// Start fresh in this tab; history lasts until refresh or New chat.
newChatButton.addEventListener("click", () => {
    if (isSending) return;
    messages.length = 0;
    chat.replaceChildren();
    chat.hidden = true;
    welcome.hidden = false;
    conversationTitle.textContent = "New conversation";
    input.value = "";
    setSending(false);
    resizeInput();
    followOutput = true;
    conversation.scrollTop = 0;
    setSidebar(false);
    input.focus({ preventScroll: true });
});
document.querySelectorAll(".suggestion").forEach((suggestion) => {
    suggestion.addEventListener("click", () => {
        if (isSending) return;
        input.value = suggestion.dataset.prompt;
        resizeInput();
        input.focus();
    });
});

function setSidebar(open) {
    const drawerOpen = open && mobileLayout.matches;
    document.body.classList.toggle("sidebar-open", drawerOpen);
    menuButton.setAttribute("aria-expanded", String(drawerOpen));
    sidebar.inert = mobileLayout.matches && !drawerOpen;
    mainContent.inert = drawerOpen;
    if (drawerOpen) document.getElementById("closeSidebar").focus();
}
function closeSidebar() {
    setSidebar(false);
    if (mobileLayout.matches) menuButton.focus();
}
menuButton.addEventListener("click", () => setSidebar(true));
document.getElementById("closeSidebar").addEventListener("click", closeSidebar);
document.getElementById("sidebarBackdrop").addEventListener("click", closeSidebar);
document.getElementById("currentChatButton").addEventListener("click", () => {
    closeSidebar();
    scrollToBottom(true);
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) closeSidebar();
});
mobileLayout.addEventListener("change", () => setSidebar(false));
setSidebar(false);
resizeInput();
