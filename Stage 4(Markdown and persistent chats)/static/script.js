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
const storageNotice = document.getElementById("storageNotice");

// One saved conversation for Stage 4, separate from other apps/stages.
const STORAGE_KEY = "llm-wrapper.stage4.messages.v1";
const messages = [];
let isSending = false;
let followOutput = true;

function showStorageNotice(text) {
    storageNotice.textContent = text;
    storageNotice.hidden = !text;
}

function saveMessages() {
    try {
        if (messages.length === 0) {
            localStorage.removeItem(STORAGE_KEY);
        } else {
            // localStorage stores strings, so serialize the conversation.
            localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
        }
        showStorageNotice("");
        return true;
    } catch {
        showStorageNotice(messages.length
            ? "This browser couldn't save the chat. New messages may be lost after refresh."
            : "The new chat is open, but the old saved copy could not be cleared. It may return after refresh.");
        return false;
    }
}

function loadConversation() {
    if (isSending) return;
    let savedMessages;
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        // Parse stored JSON back into message objects, then validate them.
        savedMessages = saved === null ? [] : JSON.parse(saved);
        if (!Array.isArray(savedMessages) || !savedMessages.every(message =>
            message && ["user", "assistant"].includes(message.role) &&
            typeof message.content === "string"
        )) {
            throw new Error("Invalid saved conversation.");
        }
    } catch {
        showStorageNotice("The saved chat couldn't be loaded. You can still chat, or use New chat to clear it.");
        return;
    }

    // A final user message without a reply means generation was interrupted.
    // Keep the saved copy until retry, so another refresh also restores it.
    const pendingMessage = savedMessages.at(-1)?.role === "user"
        ? savedMessages.pop().content : "";

    messages.length = 0;
    chat.replaceChildren();
    chat.hidden = true;
    welcome.hidden = false;
    followOutput = false;
    for (const message of savedMessages) {
        messages.push({ role: message.role, content: message.content });
        if (message.role === "user") {
            addMessage("You", message.content);
        } else {
            const content = addMessage("AI", "");
            // Stored Markdown is untrusted too: sanitize it again on restore.
            renderAssistantMessage(content, message.content);
        }
    }
    conversationTitle.textContent = messages.find(message => message.role === "user")?.content
        || pendingMessage || "New conversation";
    input.value = pendingMessage;
    setSending(false);
    resizeInput();
    scrollToBottom(true);
    showStorageNotice(pendingMessage
        ? "The last reply was interrupted. Your message is ready to send again."
        : "");
}

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

// User messages, errors, and placeholders always start as literal text.
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

function renderAssistantMessage(content, markdown) {
    try {
        if (!window.marked?.parse || !window.DOMPurify?.isSupported) {
            throw new Error("Markdown libraries are unavailable.");
        }

        // Reparse the whole accumulated reply: Markdown can span many chunks.
        const html = marked.parse(markdown, { gfm: true, breaks: false, async: false });
        const safeContent = DOMPurify.sanitize(html, {
            ALLOWED_TAGS: [
                "p", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                "strong", "em", "del", "ul", "ol", "li", "blockquote",
                "pre", "code", "hr", "table", "thead", "tbody", "tr",
                "th", "td", "a"
            ],
            ALLOWED_ATTR: ["href", "title", "start", "align"],
            ALLOW_DATA_ATTR: false,
            ALLOW_ARIA_ATTR: false,
            RETURN_DOM_FRAGMENT: true
        });

        // Only sanitized nodes enter the page; never insert raw model HTML.
        content.replaceChildren(safeContent);
        content.classList.add("markdown-content");
    } catch {
        // A loading or rendering problem must still leave a readable reply.
        content.classList.remove("markdown-content");
        content.textContent = markdown;
    }
}

async function sendMessage() {
    const message = input.value.trim();
    if (!message || isSending) return;

    setSending(true);
    if (messages.length === 0) conversationTitle.textContent = message;
    messages.push({ role: "user", content: message });
    saveMessages();
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
                renderAssistantMessage(assistantText, reply);
                scrollToBottom();
            }
        }

        reply += decoder.decode();
        if (!reply.trim()) throw new Error("The model returned an empty reply. Please try again.");
        renderAssistantMessage(assistantText, reply);
        // Keep the original Markdown in history, not the generated HTML.
        messages.push({ role: "assistant", content: reply });
        saveMessages();
    } catch (error) {
        // Restore the failed turn as a draft, ready to retry.
        messages.pop();
        saveMessages();
        input.value = message;
        if (reply) {
            renderAssistantMessage(assistantText, reply);
        } else {
            assistantText.textContent = "No reply received.";
        }
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

// Start fresh and remove only this app's saved conversation.
newChatButton.addEventListener("click", () => {
    if (isSending) return;
    messages.length = 0;
    saveMessages();
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
loadConversation();
