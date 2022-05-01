$(document).ready(function(){

var createSpaceModal = document.getElementById("create-space");
var createSpaceButton = document.getElementById("create-space-button");
var closeSpaceModal = document.getElementById("close-create-space");

//createSpaceButton.onclick = function() {
 // createSpaceModal.style.display = "block";
//}


window.onclick = function(event) {
  if (event.target == createSpaceModal) {
    createSpaceModal.style.display = "none";
  }
}

  $(document).on('click', '#create-space-button', function() {
    createSpaceModal.style.display = "block";
  });
  $(document).on('click', '#close-create-space', function() {
    createSpaceModal.style.display = "none";
  });
  
  //$(document).on('click', function(e) {
  //  if(!(($(e.target).closest("#modalBox").length > 0 ) || ($(e.target).closest("#modal-btn").length > 0))){
  //  $("#modalBox").hide();
 //  }
 // });
  
   
  $("#chat").on('mouseenter', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "visible");
  });
  $("#chat").on('mouseleave', '.message-container-combine', function(){
    $(this).find(".message-combine-time").css("visibility", "hidden");
  });
});
