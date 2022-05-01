$(document).ready(function(){

var createSpaceModal = document.getElementById("create-space");
var createSpaceButton = document.getElementById("create-space-button");
var closeSpaceModal = document.getElementById("close-create-space");

//createSpaceButton.onclick = function() {
 // createSpaceModal.style.display = "block";
//}

closeSpaceModal.onclick = function() {
  createSpaceModal.style.display = "none";
}

window.onclick = function(event) {
  if (event.target == createSpaceModal) {
    createSpaceModal.style.display = "none";
  }
}

  $(document).on('click', '#create-space-button', function() {
    createSpaceModal.style.display = "block";
  });

  $("#chat").on('mouseenter', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "visible");
  });
  $("#chat").on('mouseleave', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "hidden");
  });
});
