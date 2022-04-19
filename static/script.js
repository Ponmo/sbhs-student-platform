$(document).ready(function(){
  var chatDiv = document.getElementById("chat");
  var element = document.getElementById("submit"); //grab the element
  element.onclick = function() { 
    chatDiv.scrollTop = chatDiv.scrollHeight; 
  }
  var textarea = document.getElementById("message_input");
  var limit = 200;
  textarea.oninput = function() {
    textarea.style.height = "";
    textarea.style.height = Math.min(textarea.scrollHeight, 300) + "px";
  }; 
  $("#message_input").keypress(function (e) {
    if(e.which === 13 && !e.shiftKey) {
        e.preventDefault();
    
        $(this).closest("form").submit();
        chatDiv.scrollTop = chatDiv.scrollHeight; 
//       put placeholder message (message is loading but is faded out to account for lag time or disconnected if wanted)
    }
  });
  $(".space").click(function() {
    $.ajax({
      type: "POST",
      url: '/space',
      dataType: "json",
      data: JSON.stringify([{'space_i': 'test'}]), //JSOn.stringify
      contentType: 'application/json;charset=UTF-8',
      success: function(result) {
        alert(JSON.stringify(result));
      } 
    });
  });
  $("#chat").on('mouseenter', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "visible");
  });
  $("#chat").on('mouseleave', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "hidden");
  });
});
