$(document).ready(function () {
    // Socket.IO configuration with reconnection settings
    var socket = io({
        reconnection: true,             // Ensure reconnection is enabled
        reconnectionAttempts: Infinity, // Retry indefinitely until connected
        reconnectionDelay: 1000,        // Wait 1 second before the first attempt
        reconnectionDelayMax: 5000,     // Maximum delay between retries is 5 seconds
        timeout: 20000,                 // Wait 20 seconds for a response from the server
    });

    var currentIndex = 0;
    var messages = [];
    var timeoutId = null;
    var backupTimeoutId = null;
    var isDisplaying = false;
    var totalMessagesReceived = 0;
    var isConnected = true;  // Flag to track connection state

    // Handle both initial and new messages in a unified way
    function addMessages(newMessages) {
        if (!newMessages || !Array.isArray(newMessages)) {
            console.error("Invalid data format for messages:", newMessages);
            return;
        }

        // Add new messages and keep only unique ones
        newMessages.forEach((newMessage) => {
            if (!messages.some((message) => message.id === newMessage.id)) {
                messages.push(newMessage);
            }
        });

        totalMessagesReceived += newMessages.length;

        console.log("Number of new messages received:", newMessages.length);
        console.log("Total messages received from server so far:", totalMessagesReceived);

        // Sort messages by time descending
        messages = messages.sort((a, b) => new Date(b.time) - new Date(a.time));

        console.debug("Messages updated, new message list:", messages);
        console.debug("isDisplaying before showing the next message:", isDisplaying);

        if (!isDisplaying && isConnected) {
            showMessage();  
        }
    }

    // Handle successful connection
    socket.on('connect', function () {
        console.log('Successfully connected to the server.');
        $("#push-indicator").hide();  // Hide the lost connection message when reconnected
        isConnected = true;  // Set connection flag to true
        if (!isDisplaying && messages.length > 0) {
            showMessage();  // Resume showing messages when reconnected
        }
    });

    // Listen for refresh events and reload the page when triggered
    socket.on('refresh', function (data) {
        console.log("Received refresh event from server:", data);
        location.reload();  // Reload the page on refresh event
    });

    // Handle connection loss
    socket.on('disconnect', function () {
        console.warn('Lost connection to the server.');
        $("#push-indicator").text('החיבור לשרת אבד. מנסה להתחבר מחדש...').show();  // Show lost connection message
        isConnected = false;  // Set connection flag to false
        stopMessageDisplay();  // Stop displaying messages until reconnected
    });

    // Handle reconnection attempts
    socket.on('reconnect_attempt', function (attempt) {
        console.log(`Reconnection attempt ${attempt}`);
    });

    // Handle reconnection success
    socket.on('reconnect', function () {
        console.log('Reconnected to the server.');
        $("#push-indicator").hide();  // Hide the lost connection message when reconnected
        isConnected = true;  // Set connection flag to true
        if (!isDisplaying && messages.length > 0) {
            showMessage();  // Resume showing messages when reconnected
        }
    });

    // Handle reconnection failure after max attempts
    socket.on('reconnect_failed', function () {
        console.error('Failed to reconnect to the server.');
        $("#push-indicator").text('לא ניתן להתחבר לשרת. אנא נסה שוב מאוחר יותר.').show();  // Show permanent lost connection message
        isConnected = false;  // Ensure the flag remains false if reconnection fails
    });

    // Listen for initial messages from server
    socket.on('initial_messages', function (data) {
        console.debug("Received initial messages:", data);
        addMessages(data.messages);
        console.log("Number of initial messages received:", data.messages.length);
    });

    // Handle incoming new messages
    socket.on('new_message', function (data) {
        console.debug("Received new message:", data);
        addMessages([data]);  
    });

    socket.on('total_processed', function (data) {
        console.log('Total messages processed by server:', data.count);
    });

    // Stop displaying messages when disconnected
    function stopMessageDisplay() {
        clearTimeout(timeoutId);
        clearTimeout(backupTimeoutId);
        isDisplaying = false;
    }

    // Function to extract and display message content
    function extractMessageContent(messageData) {
        console.debug("Extracting message content:", messageData);

        let messageText = messageData.message || "";
        if (typeof messageText !== 'string') {
            console.error("messageText is not a string:", messageText);
            messageText = String(messageText);
        }

        $("#message-media").html(''); 

        const imageRegex = /<img[^>]+src="([^"]+)"[^>]*>/g;
        const videoRegex = /<video[^>]+src="([^"]+)"[^>]*>/g;

        let cleanedMessage = messageText.replace(imageRegex, '').replace(videoRegex, '');

        let imageMatch;
        while ((imageMatch = imageRegex.exec(messageText)) !== null) {
            if (imageMatch[1]) {
                const imageUrl = imageMatch[1];
                console.debug("Adding image to message media:", imageUrl);
                $("#message-media").append(`<img src="${imageUrl}" alt="Photo" class="message-image">`);
            }
        }

        let videoMatch;
        while ((videoMatch = videoRegex.exec(messageText)) !== null) {
            if (videoMatch[1]) {
                const videoUrl = videoMatch[1];
                console.debug("Adding video to message media:", videoUrl);
                $("#message-media").append(`
                    <video controls autoplay class="message-video">
                        <source src="${videoUrl}" type="video/mp4">
                        Your browser does not support the video tag.
                    </video>
                `);
            }
        }

        return cleanedMessage;
    }

    // Function to show a message
    function showMessage() {
        if (!isConnected) {
            console.warn("Cannot display messages while disconnected.");
            return;  // Don't show messages while disconnected
        }

        if (messages.length === 0) {
            console.warn("No messages to display yet.");
            isDisplaying = false;
            return;
        }

        if (currentIndex >= messages.length) {
            currentIndex = 0; 
        }

        var messageData = messages[currentIndex];
        if (messageData) {
            isDisplaying = true;
            console.debug("Showing message: ", messageData);

            var channelName = messageData.channel;
            var message = extractMessageContent(messageData);

            var messageDateUTC = new Date(messageData.time);
            if (isNaN(messageDateUTC.getTime())) {
                console.error("Invalid message date:", messageData.time);
                return;
            }            
            var currentTime = new Date(); 
            var currentTimeUTC = new Date(currentTime.toISOString()); 

            var timeDifference = (currentTimeUTC - messageDateUTC) / (1000 * 60);

            console.debug("Message date UTC:", messageDateUTC);
            console.debug("Current time UTC:", currentTimeUTC);

            var options = {
                timeZone: 'Asia/Jerusalem',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            };
            var messageTime = new Intl.DateTimeFormat('he-IL', options).format(messageDateUTC);
    
            $("#channel-name").text(channelName);
            $("#message-text").html(message);
    
            if (timeDifference <= 60) {  
                $("#message-time").removeClass("old-message").addClass("recent-message");
            } else {
                $("#message-time").removeClass("recent-message").addClass("old-message");
            }
    
            $("#message-time").text("נשלח ב: " + messageTime);
    
            if (messageData.is_push) {
                $("#push-indicator").text("הודעת פוש התקבלה!").show();
            } else {
                $("#push-indicator").hide();
            }

            currentIndex = (currentIndex + 1) % messages.length;

            clearTimeout(timeoutId);
            clearTimeout(backupTimeoutId);

            timeoutId = setTimeout(function () {
                console.debug("Timeout for next message reached, moving to next message.");
                isDisplaying = false;
                showMessage();
            }, 5000);

            backupTimeoutId = setTimeout(function () {
                if (isDisplaying) {
                    console.debug("Backup timeout reached, forcing next message.");
                    isDisplaying = false;
                    showMessage();
                }
            }, 15000);
        } else {
            isDisplaying = false;
        }
    }

    $("#refreshFeed").on('click', function () {
        location.reload();
    });
});
