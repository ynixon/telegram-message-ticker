$(document).ready(function () {
    // 1. Check if Socket.IO is loaded
    if (typeof io === 'undefined') {
        console.error("Socket.IO is not loaded. Please check the script tag and integrity attribute.");
        alert("Failed to load Socket.IO. Please check the browser console for more details.");
        return; // Exit the script to prevent further errors
    }

    // 2. Initialize Socket.IO
    var socket = io();
    var currentIndex = 0;
    var messages = [];
    var timeoutId;
    var isDisplaying = false;

    // 3. Listen for initial messages
    socket.on('initial_messages', function(data) {
        if (!data.messages || !Array.isArray(data.messages)) {
            console.error("Invalid data format for initial_messages:", data);
            return;
        }

        // Sort messages by time descending
        messages = data.messages.sort((a, b) => new Date(b.time) - new Date(a.time));

        if (messages.length > 0) {
            currentIndex = 0;
            showMessage();
        }
    });

    // 4. Listen for new messages
    socket.on('new_message', function(data) {
        if (!data.message) {
            console.debug("Received new_message without 'message' field:", data);
            return;
        }

        // Add new message to the beginning
        messages.unshift(data.message);
        console.debug("New message received:", data.message);

        // If currently displaying a message, interrupt and show the new one
        if (isDisplaying) {
            clearTimeout(timeoutId);
            currentIndex = 0;
            showMessage();
        } else {
            // If not displaying, start showing messages
            if (messages.length === 1) { // Only the new message exists
                showMessage();
            }
        }
    });

    // 5. Function to extract and display message content
    function extractMessageContent(messageData) {
        console.debug("Message Data:", messageData);

        let messageText = messageData.message || "";
        $("#message-media").html(''); // Clear previous media

        const imageRegex = /<img[^>]+src="([^"]+)"[^>]*>/g;
        const videoRegex = /<video[^>]+src="([^"]+)"[^>]*>/g;
        let cleanedMessage = messageText.replace(imageRegex, '').replace(videoRegex, '');

        // Handle images
        let imageMatch = messageText.match(imageRegex);
        if (imageMatch) {
            const imageUrlMatch = imageMatch[0].match(/src="([^"]+)"/);
            if (imageUrlMatch && imageUrlMatch[1]) {
                const imageUrl = imageUrlMatch[1];
                $("#message-media").append(`<img src="${imageUrl}" alt="Photo" class="message-image">`);
            }
        }

        // Handle videos
        let videoMatch = messageText.match(videoRegex);
        if (videoMatch) {
            const videoUrlMatch = videoMatch[0].match(/src="([^"]+)"/);
            if (videoUrlMatch && videoUrlMatch[1]) {
                const videoUrl = videoUrlMatch[1];
                $("#message-media").append(`
                    <video controls autoplay class="message-video">
                        <source src="${videoUrl}" type="video/mp4">
                        Your browser does not support the video tag.
                    </video>
                `);
            }
        }

        return convertUrlsToLinks(cleanedMessage);
    }

    // 6. Function to convert URLs in text to clickable links
    function convertUrlsToLinks(text) {
        const urlPattern = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
        return text.replace(urlPattern, '<a href="$1" target="_blank">$1</a>');
    }

    // 7. Function to display messages one by one
    function showMessage() {
        if (currentIndex >= messages.length) {
            currentIndex = 0;  // Reset index if out of bounds
            return;
        }

        var messageData = messages[currentIndex];
        if (messageData) {
            isDisplaying = true;
            console.debug("Showing message: ", messageData);
            var channelName = messageData.channel;
            var message = extractMessageContent(messageData);

            // Updated messageTime to explicitly convert from UTC to the correct timezone
            var messageDateUTC = new Date(messageData.time + 'Z'); // Ensure the time is treated as UTC by appending 'Z'
            var options = { timeZone: 'Asia/Jerusalem', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
            var messageTime = new Intl.DateTimeFormat('he-IL', options).format(messageDateUTC);


            var currentTime = new Date();
            var messageDate = new Date(messageData.time);
            var timeDifference = (currentTime - messageDate) / (1000 * 60);  // in minutes

            $("#channel-name").text(channelName);
            $("#message-text").html(message);

            // Change text color based on message age
            if (timeDifference <= 60) {  // Less than or equal to 1 hour
                $("#message-time").css("color", "green");
            } else {
                $("#message-time").css("color", "");
            }

            $("#message-time").text("נשלח ב: " + messageTime);

            currentIndex++;

            clearTimeout(timeoutId);

            // Check if the message contains a video
            var videoElement = $("#message-media").find("video");
            if (videoElement.length > 0) {
                videoElement.on('ended', function () {
                    isDisplaying = false;
                    showMessage();
                });
            } else {
                // Set timeout to show next message after 5 seconds
                timeoutId = setTimeout(function() {
                    isDisplaying = false;
                    showMessage();
                }, 5000);
            }
        }
    }

    // 8. Refresh Feed Button Handler
    $("#refreshFeed").on('click', function() {
        location.reload();  // Reload the page to fetch new messages
    });
});