$(document).ready(function(){
  // Get the modal
var modal = document.getElementById("create-space");

// Get the button that opens the modal
var btn = document.getElementById("create");

// Get the <span> element that closes the modal
var span = document.getElementsByClassName("close")[0];

// When the user clicks on the button, open the modal
btn.onclick = function() {
  modal.style.display = "block";
}

// When the user clicks on <span> (x), close the modal
span.onclick = function() {
  modal.style.display = "none";
}

// When the user clicks anywhere outside of the modal, close it
window.onclick = function(event) {
  if (event.target == modal) {
    modal.style.display = "none";
  }
}

  var textarea = document.getElementById("message_input");
  textarea.oninput = function() {
    textarea.style.height = "";
    textarea.style.height = Math.min(textarea.scrollHeight, 300) + "px";
  }; 
  $("#message_input").keypress(function (e) {
    isTyping();
    if(e.which === 13 && !e.shiftKey) {
        e.preventDefault();
    
        $(this).closest("form").submit();
        chatDiv.scrollTop = chatDiv.scrollHeight; 
        textarea.setAttribute("style", "");
        textarea.value = "";
        stoppedTyping();
//       put placeholder message (message is loading but is faded out to account for lag time or disconnected if wanted)
    }
  });
  $("#chat").on('mouseenter', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "visible");
  });
  $("#chat").on('mouseleave', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "hidden");
  });
});
