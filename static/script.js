$(document).ready(function(){

  var createSpaceModal = document.getElementById("create-space");
  var createSpaceButton = document.getElementById("create-space-button");
  var closeSpaceModal = document.getElementById("close-create-space");

  $(document).on('click', '#create-space-button', function() {
    createSpaceModal.style.display = "block";
  });
  $(document).on('click', '#close-create-space', function() {
    createSpaceModal.style.display = "none";
  });
  window.onclick = function(event) {
    if (event.target == createSpaceModal) {
      createSpaceModal.style.display = "none";
    }
  }
  
  //var createRoomModal = document.getElementById("create-room");
  //var createRoomButton = document.getElementById("create-room-button");
  //var closeRoomModal = document.getElementById("close-create-room");

  /*$(document).on('click', '#create-space-button', function() {
    createSpaceModal.style.display = "block";
  });
  $(document).on('click', '#close-create-space', function() {
    createSpaceModal.style.display = "none";
  });
  window.onclick = function(event) {
    if (event.target == createSpaceModal) {
      createSpaceModal.style.display = "none";
    }
  }*/
   
  $("#chat").on('mouseenter', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "visible");
  });
  $("#chat").on('mouseleave', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "hidden");
  });
});
