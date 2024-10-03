$(document).ready(function () {
    var currentIndex = 0;
    var messages = [];
    var timeoutId;
    var messageAgeLimit = 2;  // Default value, in hours

    function updateTicker() {
        console.log("Fetching messages...");
    
        $("#message-text").html("<span>טוען נתונים, נא להמתין...</span>");
        $("#channel-name").text('');
        $("#message-time").text('');
        $("#message-media").html("");
    
        $.get("/getMessages", function (data) {
            console.log("Fetched data: ", data);
    
            if (data && data.messages && Array.isArray(data.messages)) {
                messages = data.messages;
                if (messages.length > 0) {
                    currentIndex = 0;
                    showMessage();
                } else {
                    $("#message-text").html("<span>אין הודעות זמינות</span>");
                    $("#message-media").html("");
                    setTimeout(updateTicker, 5000); // Retry after 5 seconds
                }
            } else if (data.status) {
                console.warn("Status message received:", data.status);
                $("#message-text").html("<span>" + data.status + "</span>");
                setTimeout(updateTicker, 5000);  // Retry after 5 seconds
            } else {
                console.error("Unexpected data format:", data);
                $("#message-text").html("<span>תקלה בהבאת הודעות</span>");
                $("#message-media").html("");
                setTimeout(updateTicker, 5000);  // Retry after 5 seconds
            }
        })
        .fail(function (jqXHR, textStatus, error) {
            console.error("Failed to fetch messages:", textStatus, error);
            $("#message-text").html("<span>תקלה בהבאת הודעות</span>");
            $("#message-media").html("");
            setTimeout(updateTicker, 5000);  // Retry after 5 seconds
        });
    }
    

    function extractMessageContent(messageData) {
        console.log("Message Data:", messageData);

        let messageText = messageData.message || "";
        $("#message-media").html('');

        const imageRegex = /<img[^>]+src="([^">]+)"[^>]*>/g;
        let cleanedMessage = messageText.replace(imageRegex, '');

        let imageMatch = messageText.match(imageRegex);
        if (imageMatch) {
            const imageUrl = imageMatch[0].match(/src="([^">]+)"/)[1];
            $("#message-media").html(`<img src="${imageUrl}" alt="Photo" class="message-image">`);
        }

        return convertUrlsToLinks(cleanedMessage);
    }

    function convertUrlsToLinks(text) {
        const urlPattern = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
        return text.replace(urlPattern, '<a href="$1" target="_blank">$1</a>');
    }

    function showMessage() {
        if (currentIndex >= messages.length) {
            currentIndex = 0;
            setTimeout(updateTicker, 0);
            return;
        }

        var messageData = messages[currentIndex];
        if (messageData) {
            console.log("Showing message: ", messageData);
            var channelName = messageData.channel;
            var message = extractMessageContent(messageData);
            var messageTime = new Date(messageData.time).toLocaleString();

            var currentTime = new Date();
            var messageDate = new Date(messageData.time);
            var timeDifference = (currentTime - messageDate) / (1000 * 60);

            $("#channel-name").text(channelName);
            $("#message-text").html(message);

            if (timeDifference <= 60) {
                $("#message-time").css("color", "green");
            } else {
                $("#message-time").css("color", "");
            }

            $("#message-time").text("נשלח ב: " + messageTime);

            currentIndex++;

            clearTimeout(timeoutId);
            timeoutId = setTimeout(showMessage, 5000);
        }
    }

    updateTicker();

    $("#refreshFeed").on('click', function() {
        updateTicker();
    });
});
