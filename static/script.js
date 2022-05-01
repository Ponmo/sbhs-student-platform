$(document).ready(function(){

  var createSpaceModal = document.getElementById("create-space");

  $(document).on('click', '#create-space-button', function() {
    createSpaceModal.style.display = "block";
  });
  $(document).on('click', '#close-create-space', function() {
    createSpaceModal.style.display = "none";
  });
  
  var createRoomModal = document.getElementById("create-room");

  $(document).on('click', '.create-room-button', function() {
    createRoomModal.style.display = "block";
  });
  $(document).on('click', '#close-create-room', function() {
    createRoomModal.style.display = "none";
  });
  window.onclick = function(event) {
    if (event.target == createRoomModal) {
      createRoomModal.style.display = "none";
    }
    if (event.target == createSpaceModal) {
      createSpaceModal.style.display = "none";
    }
  }
   
  $("#chat").on('mouseenter', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "visible");
  });
  $("#chat").on('mouseleave', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "hidden");
  });
});
