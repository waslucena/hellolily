$(document).ready(function(){set_focus("id_email")});$(document).ready(function(){function a(){$(".mws-form-formset").formset({formTemplate:".formset-custom-template",prefix:"form",addText:gettext("Add another"),preventEmptyFormset:true,added:function(){$.tabthisbody()},addCssClass:"add-row mws-form-row",deleteCssClass:"invitation-delete-row",})}a();$("#mws-form-dialog").dialog({autoOpen:false,title:gettext("User invitation form"),modal:true,width:"640",buttons:[{text:gettext("Send invitation(s)"),click:function(){sendForm($(this))}}],close:function(b,c){clearForm($(this).find("form.mws-form"))}});$("#mws-form-dialog-mdl-btn").bind("click",function(b){$("#mws-form-dialog").dialog("open");b.preventDefault()})});$(document).ready(function(){$.validator.addMethod("placeholder",function(b,a){return b!=$(a).attr("placeholder")},$.validator.messages.required);$("#mws-login-form form").validate({rules:{username:{required:true,placeholder:true},password:{required:true,placeholder:true}},errorPlacement:function(a,b){},invalidHandler:function(b,a){if($.fn.effect){$("#mws-login").effect("shake",{distance:6,times:2},35)}}});if($.fn.placeholder){$("[placeholder]").placeholder()}set_focus("id_username");$("input:checkbox").screwDefaultButtons({checked:"url("+media_url("plugins/screwdefaultbuttons/images/checkbox_checked.png")+")",unchecked:"url("+media_url("plugins/screwdefaultbuttons/images/checkbox_unchecked.png")+")",width:16,height:16})});$(document).ready(function(){set_focus("id_email");set_focus("id_new_password1")});$(document).ready(function(){set_focus("id_first_name");var c=$(".errorlist");if(c.length){var b=c[0];var a=jQuery(b).nextAll("input").eq(0);a.focus()}});